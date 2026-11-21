"""Turn the harness's raw trace rows (phase2.trace.Trace.log -> trace.jsonl) into typed UI events.

The same Normalizer feeds the live stream (rows fed one by one as the file grows) and the recorded replay (all rows
at once), so a replay looks exactly like the live run did. It only reads; prices come from the trace's own
llm_call.cost_usd, or the repo's list prices (phase2.list_prices) when a backend logged 0 (Factory Droid runs).

Event types (every event has `seq`, `t` = seconds since the trace started, `type`):
  run_config   setup policy and the models in it
  plan         plan steps
  phase_start  {phase: {id, kind, step, model, label, title, started}}  kind: plan|strategy|spec|build|fix|checks|
               review|takeover|helper
  phase_end    {phase_id, status: done|failed, ended, note}
  model_call   one finished model call (tokens in/cached/out, estimated cost, reply excerpt)
  call_failed  a model call that failed (and whether it is retried)
  activity     a smaller thing inside a phase (a review's read-only command)
  checks       the builder's check script result
  review       the judge's verdict and problems
  step_end     a plan step finished (with the builder's summary and files it reported)
  narration    one plain-language line for the chat (tone: info|good|bad|warn|handoff)
  usage        running totals per model and overall
  working      who is working right now (open phases, newest first)
  run_done     the harness finished
"""
from __future__ import annotations

import sys
from pathlib import Path

from .models import model_info, model_label

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from phase2.list_prices import call_usd  # noqa: E402

ROLE_KIND = {"worker": "build", "takeover": "takeover", "reviewer": "review", "planner": "plan",
             "strategist": "strategy", "spec": "spec", "helper": "helper", "fixer": "fix"}
KIND_TITLE = {"plan": "Plan", "strategy": "Strategy", "spec": "Spec", "build": "Build", "fix": "Builder fix",
              "checks": "Checks", "review": "Review", "takeover": "Takeover", "helper": "Helper"}
KIND_VERB = {"plan": "is planning", "strategy": "is choosing a strategy", "spec": "is writing the spec",
             "build": "is building", "fix": "is fixing the review's problems", "checks": "is running checks",
             "review": "is reviewing the work", "takeover": "has taken over", "helper": "is helping"}
EXIT_WORDS = {"Submitted": "submitted", "LimitsExceeded": "used all its turns", "TimeExceeded": "hit its time cap",
              "StepIdle": "went quiet too long", "RuntimeError": "a model call kept failing"}


def _num(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def call_cost(e: dict) -> tuple[float, str]:
    """(estimated USD, source). The trace's own figure, else list price x tokens, else 0."""
    logged = e.get("cost_usd")
    if isinstance(logged, (int, float)) and logged > 0:
        return float(logged), "trace"
    listed = call_usd(e.get("model", ""), _num(e.get("input_tokens")), _num(e.get("cached_tokens")),
                      _num(e.get("output_tokens")))
    return (listed, "list_price") if listed is not None else (0.0, "unpriced")


def count_checks(e: dict) -> tuple[int, int]:
    """PASS/FAIL lines in the check run's output only (older traces also counted the script's own text)."""
    text = e.get("text") or ""
    if "--- output" in text:
        out = text.rsplit("--- output", 1)[-1].split("--- exit code", 1)[0]
        return out.count("PASS "), out.count("FAIL ")
    return _num(e.get("passes")), _num(e.get("fails"))


def problem_text(p) -> str:
    if isinstance(p, dict):
        return str(p.get("what") or p.get("problem") or p)
    return str(p)


def reported_files(outputs) -> list[str]:
    """Paths the builder reported in its outputs (absolute, outside /tmp)."""
    files = []
    for v in (outputs or {}).values() if isinstance(outputs, dict) else []:
        for part in str(v).replace(",", " ").split():
            part = part.strip("()[]{};:'\"`").rstrip(".")
            if part.startswith("/") and not part.startswith("/tmp") and len(part) < 200 and part not in files:
                files.append(part)
    return files


class Normalizer:
    def __init__(self):
        self.seq = 0
        self.phases: dict[str, dict] = {}      # id -> phase
        self.open: dict[tuple, str] = {}        # (step, kind) -> phase id
        self.usage: dict[str, dict] = {}
        self.steps: dict[str, dict] = {}
        self.multi_step = False
        self.last_t = 0.0
        self._working_key = None
        self.counter = 0
        self.judge = ""  # the config's judge: after the checks run, the judge is the one working

    # ---------- helpers ----------
    def _ev(self, out: list, type_: str, t: float, **data) -> None:
        self.seq += 1
        out.append({"seq": self.seq, "t": round(float(t), 3), "type": type_, **data})

    def _say(self, out, t, text, tone="info", model=None):
        self._ev(out, "narration", t, text=text, tone=tone, model=model)

    def _open(self, out, t, kind, step, model, started=None, title=None) -> dict:
        self.counter += 1
        pid = f"{kind}-{step or 'run'}-{self.counter}"
        ph = {"id": pid, "kind": kind, "step": step, "model": model, "label": model_label(model) if model else "",
              "title": title or KIND_TITLE.get(kind, kind.title()), "started": round(max(started if started is not None
                                                                                       else t, 0.0), 3),
              "ended": None, "status": "running", "calls": 0, "input": 0, "cached": 0, "output": 0, "cost_usd": 0.0}
        self.phases[pid] = ph
        self.open[(step, kind)] = pid
        self._ev(out, "phase_start", t, phase=dict(ph))
        return ph

    def _close(self, out, t, step, kind, status="done", note="", ended=None) -> dict | None:
        pid = self.open.pop((step, kind), None)
        if not pid:
            return None
        ph = self.phases[pid]
        ph["ended"], ph["status"] = round(ended if ended is not None else t, 3), status
        self._ev(out, "phase_end", t, phase_id=pid, status=status, ended=ph["ended"], note=note)
        return ph

    def _end_build(self, out, t, step) -> None:
        """The builder handed over (checks, review or takeover started): its phase ends at its last reply."""
        pid = self.open.get((step, "build"))
        if pid:
            self._close(out, t, step, "build", ended=self.phases[pid].get("last_call", t))

    def _close_step(self, out, t, step, status="done"):
        for (s, k) in [key for key in self.open if key[0] == step]:
            self._close(out, t, s, k, status=status)

    def _who(self, step) -> str:
        return f" (step {step})" if self.multi_step and step else ""

    def _working(self, out, t):
        rows = sorted((self.phases[p] for p in self.open.values()), key=lambda p: -p["started"])
        now = [{"phase_id": p["id"], "kind": p["kind"], "step": p["step"], "model": p["model"], "label": p["label"],
                "verb": KIND_VERB.get(p["kind"], "is working"), "since": p["started"]} for p in rows if p["model"]]
        key = tuple((w["phase_id"]) for w in now)
        if key != self._working_key:
            self._working_key = key
            self._ev(out, "working", t, now=now)

    def _usage(self, out, t):
        rows = sorted(self.usage.values(), key=lambda r: -r["cost_usd"])
        total = {k: sum(r[k] for r in rows) for k in ("input", "cached", "output", "cost_usd", "calls")}
        self._ev(out, "usage", t, by_model=[dict(r) for r in rows], total=total)

    # ---------- main entry ----------
    def feed(self, raw: dict) -> list[dict]:
        out: list[dict] = []
        name = raw.get("event")
        t = float(raw.get("t") or 0.0)
        self.last_t = max(self.last_t, t)
        handler = getattr(self, f"_on_{name}", None)
        if handler:
            handler(raw, t, out)
        self._working(out, t)
        return out

    def feed_all(self, rows) -> list[dict]:
        out = []
        for r in rows:
            out += self.feed(r)
        return out

    # ---------- per raw event ----------
    def _on_config(self, e, t, out):
        roles = []
        tiers = e.get("tiers") or []
        seen = set()
        for mid, role in [(e.get("planner_model"), "planner")] + [(m, f"tier {i}") for i, m in enumerate(tiers)] + [
                (e.get("senior_reviewer_model"), "judge"), (e.get("takeover_model"), "takeover")]:
            if mid and (mid, role) not in seen:
                seen.add((mid, role))
                if role == "judge":
                    self.judge = mid
                roles.append({**model_info(mid), "role": role})
        self._ev(out, "run_config", t, policy=e.get("policy", ""), models=roles,
                 verified_done=bool(e.get("verified_done")), judge=model_label(e.get("senior_reviewer_model") or ""),
                 takeover=model_label(e.get("takeover_model") or "") if e.get("takeover_model") else "")

    def _on_plan(self, e, t, out):
        steps = [{"id": s.get("id"), "goal": (s.get("goal") or "").strip(), "depends_on": s.get("depends_on") or []}
                 for s in e.get("steps") or []]
        self.multi_step = len(steps) > 1
        for s in steps:
            self.steps[s["id"]] = {"status": "pending"}
        planner = e.get("planner", "")
        self._ev(out, "plan", t, steps=steps, planner=planner, planner_model=model_label(e.get("planner_model") or ""))
        ph = self.open.get((None, "plan"))
        if ph:
            self._close(out, t, None, "plan")
        if planner == "skipped" or len(steps) <= 1:
            self._say(out, t, "One step, so no planner: the builder starts straight away.")
        else:
            self._say(out, t, f"{model_label(e.get('planner_model') or '')} split the task into {len(steps)} steps.")

    def _on_route(self, e, t, out):
        step, model = e.get("step"), e.get("model", "")
        self._close(out, t, step, "build", status="failed")  # a re-route replaces an earlier builder
        self._open(out, t, "build", step, model)
        self.steps.setdefault(step, {})["status"] = "running"
        src = e.get("source", "")
        if src and src != "fixed":
            score = f", score {e['score']:.2f}" if isinstance(e.get("score"), (int, float)) else ""
            conf = f", confidence {e['confidence']:.2f}" if isinstance(e.get("confidence"), (int, float)) else ""
            self._say(out, t, f"Router ({src}{score}{conf}) sent{self._who(step) or ' the step'} to "
                              f"{model_label(model)}.", "handoff", model)
        self._say(out, t, f"{model_label(model)} is building{self._who(step)}.", "info", model)

    def _on_llm_call(self, e, t, out):
        role, step, model = e.get("role", "worker"), e.get("step"), e.get("model", "")
        kind = ROLE_KIND.get(role, role)
        started = t - _num(e.get("ms")) / 1000
        if kind == "build" and (step, "fix") in self.open:
            kind = "fix"
        pid = self.open.get((step, kind))
        if pid and self.phases[pid]["model"] and self.phases[pid]["model"] != model and kind == "build":
            self._close(out, started, step, "build", status="failed", note="escalated")
            pid = None
        if not pid:
            if kind in ("review", "takeover", "fix"):
                self._end_build(out, started, step)
            ph = self._open(out, t, kind, step, model, started=started)
            if kind == "review":
                self._say(out, t, f"{model_label(model)} is reviewing the work{self._who(step)}.", "info", model)
            elif kind == "build":
                self._say(out, t, f"{model_label(model)} is building{self._who(step)}.", "info", model)
        else:
            ph = self.phases[pid]
        inp, cached, outp = _num(e.get("input_tokens")), _num(e.get("cached_tokens")), _num(e.get("output_tokens"))
        cost, source = call_cost(e)
        ph["calls"] += 1
        ph["last_call"] = t
        ph["input"] += inp; ph["cached"] += cached; ph["output"] += outp; ph["cost_usd"] += cost
        self._ev(out, "model_call", t, phase_id=ph["id"], step=step, role=role, model=model,
                 label=model_label(model), ms=_num(e.get("ms")), input=inp, cached=cached, output=outp,
                 cost_usd=round(cost, 6), cost_source=source, reply=(e.get("reply") or "")[:1200],
                 session=e.get("session", ""))
        u = self.usage.setdefault(model, {**model_info(model), "input": 0, "cached": 0, "output": 0,
                                          "cost_usd": 0.0, "calls": 0})
        u["input"] += inp; u["cached"] += cached; u["output"] += outp; u["cost_usd"] += cost; u["calls"] += 1
        self._usage(out, t)

    def _on_llm_call_failed(self, e, t, out):
        model = e.get("model", "")
        pid = self.open.get((e.get("step"), ROLE_KIND.get(e.get("role", ""), "")))
        self._ev(out, "call_failed", t, phase_id=pid, model=model, label=model_label(model), role=e.get("role"),
                 kind=e.get("kind", ""), error=str(e.get("error", ""))[:300], will_retry=bool(e.get("will_retry")))
        retry = ", retrying" if e.get("will_retry") else ""
        self._say(out, t, f"A call to {model_label(model)} failed ({e.get('kind', 'error')}){retry}.", "warn", model)

    def _on_explore(self, e, t, out):
        pid = next((p for (s, k), p in self.open.items() if k == "review"), None)
        self._ev(out, "activity", t, phase_id=pid, text=f"read-only check: {str(e.get('command', ''))[:160]}")

    def _on_checks_run(self, e, t, out):
        step = e.get("step")
        self._end_build(out, t, step)  # the builder has submitted
        passes, fails = count_checks(e)
        ok = bool(e.get("found", True)) and str(e.get("exit", "")) == "0" and fails == 0
        secs = float(e.get("seconds") or 0)
        self._open(out, t, "checks", step, "", started=t - secs)
        self._close(out, t, step, "checks", status="done" if ok else "failed",
                    note=f"{passes} passed, {fails} failed")
        output = (e.get("text") or "").rsplit("--- output", 1)[-1].strip()
        self._ev(out, "checks", t, step=step, passes=passes, fails=fails, found=bool(e.get("found", True)),
                 exit=str(e.get("exit", "")), seconds=secs, output=output[-1500:])
        if not e.get("found", True):
            self._say(out, t, "Checks: the builder wrote no check script.", "warn")
        else:
            self._say(out, t, f"Checks: {passes} passed, {fails} failed.", "good" if ok else "bad")
        if self.judge and (step, "review") not in self.open:
            self._open(out, t, "review", step, self.judge)
            self._say(out, t, f"{model_label(self.judge)} is reviewing the work{self._who(step)}.", "info", self.judge)

    def _on_senior_review(self, e, t, out):
        step, model = e.get("step"), e.get("model", "")
        self._end_build(out, t, step)
        if (step, "review") not in self.open:
            self._open(out, t, "review", step, model)
        verdict = e.get("verdict") or ("fix" if e.get("error") else "")
        problems = [problem_text(p) for p in e.get("problems") or []]
        self._close(out, t, step, "review", status="done", note=verdict)
        self._ev(out, "review", t, step=step, model=model, label=model_label(model), verdict=verdict,
                 problems=problems, round=e.get("round", 1), error=str(e.get("error", ""))[:300])
        if e.get("error"):
            self._say(out, t, f"{model_label(model)} gave no verdict, so the work counts as needing a fix.", "warn",
                      model)
        elif verdict == "ship":
            self._say(out, t, f"{model_label(model)} reviewed: ship.", "good", model)
        else:
            n = len(problems)
            self._say(out, t, f"{model_label(model)} reviewed: {verdict or 'fix'}"
                              f"{f' ({n} problem' + ('s' if n != 1 else '') + ')' if n else ''}.", "bad", model)

    def _on_takeover(self, e, t, out):
        step, model = e.get("step"), e.get("model", "")
        if e.get("phase") == "start":
            self._end_build(out, t, step)
            self._open(out, t, "takeover", step, model)
            n = len(e.get("problems") or [])
            what = f" to fix {n} problem{'s' if n != 1 else ''}" if n else ""
            self._say(out, t, f"Handoff: {model_label(model)} takes over{what}.", "handoff", model)
        else:
            ex = e.get("exit_status", "")
            ph = self._close(out, t, step, "takeover", status="done" if ex == "Submitted" else "failed",
                             note=str(e.get("summary") or "")[:600])
            if ph:
                ph["summary"] = e.get("summary") or ""
            self.steps.setdefault(step, {})["takeover_summary"] = e.get("summary") or ""
            self._say(out, t, f"{model_label(model)} finished the takeover: {EXIT_WORDS.get(ex, ex or 'ended')}"
                              f" after {_num(e.get('n_calls'))} calls.", "good" if ex == "Submitted" else "warn", model)

    def _on_builder_fix(self, e, t, out):
        step, model = e.get("step"), e.get("model", "")
        if e.get("phase") == "start":
            self._end_build(out, t, step)
            self._open(out, t, "fix", step, model)
            n = len(e.get("problems") or [])
            self._say(out, t, f"{model_label(model)} gets the review's {n} problem{'s' if n != 1 else ''} back to "
                              f"fix first.", "handoff", model)
        else:
            ex = e.get("exit_status", "")
            self._close(out, t, step, "fix", status="done" if ex == "Submitted" else "failed")

    def _on_takeover_skipped(self, e, t, out):
        self._close(out, t, e.get("step"), "review", status="done", note="skipped")
        self._say(out, t, f"No review or takeover: the builder {EXIT_WORDS.get(e.get('builder_exit'), 'stopped')}.",
                  "warn")

    def _on_strategy(self, e, t, out):
        self._close(out, t, e.get("step"), "strategy")
        self._say(out, t, f"Strategy: {e.get('mode') or 'chosen'}.", "info", e.get("model"))

    def _on_spec(self, e, t, out):
        self._close(out, t, e.get("step"), "spec")

    def _on_tests_found(self, e, t, out):
        self._say(out, t, "Found tests that ship with the task; the builder is told to run them.")

    def _on_escalate(self, e, t, out):
        step = e.get("step")
        self._close(out, t, step, "build", status="failed", note="escalated")
        self._say(out, t, f"Escalating{self._who(step)}: tier {e.get('from_tier')} to tier {e.get('to_tier')} "
                          f"({EXIT_WORDS.get(e.get('reason'), e.get('reason') or 'failed')}).", "handoff")

    def _on_handoff(self, e, t, out):
        to = e.get("to_model")
        self._say(out, t, f"Handoff note written ({_num(e.get('chars')):,} characters)"
                          + (f" for {model_label(to)}." if to else "."), "handoff", to)

    def _on_stop(self, e, t, out):
        self._close_step(out, t, e.get("step"), status="failed")
        self._say(out, t, f"Stopped{self._who(e.get('step'))}: {str(e.get('reason', '')).replace('_', ' ')}"
                          + (f" ({str(e.get('detail'))[:120]})" if e.get("detail") else "") + ".", "bad")

    def _on_attempt(self, e, t, out):
        step = e.get("step")
        self._close(out, t, step, "build", status="done" if e.get("passed") else "failed")
        st = self.steps.setdefault(step, {})
        st["summary"] = e.get("summary") or ""
        st["files"] = reported_files(e.get("outputs"))
        st["exit"] = e.get("exit_status", "")

    def _on_step_end(self, e, t, out):
        step = e.get("step")
        self._close_step(out, t, step, status="done" if e.get("passed") else "failed")
        st = self.steps.setdefault(step, {})
        st["status"] = "done" if e.get("passed") else "failed"
        summary = st.get("takeover_summary") or st.get("summary") or ""
        self._ev(out, "step_end", t, step=step, passed=bool(e.get("passed")), attempts=_num(e.get("attempts")),
                 summary=summary[:1500], files=st.get("files", []), exit=st.get("exit", ""))
        if self.multi_step:
            self._say(out, t, f"Step {step} {'done' if e.get('passed') else 'failed'}.",
                      "good" if e.get("passed") else "bad")

    def _on_done(self, e, t, out):
        for (s, k) in list(self.open):
            self._close(out, t, s, k)
        self._ev(out, "run_done", t, escalations=_num(e.get("n_escalations")),
                 failed_steps=_num(e.get("n_failed_steps")), policy=e.get("policy", ""))
        self._say(out, t, "The harness is done. Waiting for Harbor's verifier.")


def normalize(rows) -> list[dict]:
    return Normalizer().feed_all(rows)


def result_event(result: dict, tests: float | None, grader: bool, seq: int, t: float) -> dict:
    """The Harbor trial's verdict (result.json) as a UI event."""
    reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    exc = result.get("exception_info") or {}
    ar = result.get("agent_result") or {}
    return {"seq": seq, "t": round(t, 3), "type": "result", "reward": reward, "tests": tests, "grader": grader,
            "exception": (f"{exc.get('exception_type')}: {str(exc.get('exception_message', ''))[:300]}"
                          if exc else ""),
            "started_at": result.get("started_at"), "finished_at": result.get("finished_at"),
            "harbor_cost_usd": ar.get("cost_usd")}
