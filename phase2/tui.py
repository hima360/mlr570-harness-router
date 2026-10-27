"""Terminal UI for the routed harness: start runs, watch who does what live, browse and stop jobs.

  .venv/bin/python -m phase2.tui

Everything shown comes from the run's own records (trace.jsonl, worker trajectories, Harbor results),
so the planner, router and worker names are the ones that actually ran, not the config defaults.
"""
import re
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, DataTable, Footer, Header, Input, Label, ListItem, ListView, RichLog,
                             Select, Static, TabbedContent, TabPane, Tree)

from . import jobs as J

ROOT = Path(__file__).resolve().parents[1]
STATUS_STYLE = {"running": "bold yellow", "passed": "bold green", "failed": "bold red", "error": "bold red",
                "stopped": "dim", "incomplete": "dim"}
STATUS_ICON = {"running": "●", "passed": "✓", "failed": "✗", "error": "!", "stopped": "■", "incomplete": "…"}
ROLE_STYLE = {"PLANNER": "bold magenta", "ROUTER": "bold cyan", "WORKER": "bold blue", "VERIFIER": "bold green",
              "ESCALATE": "bold yellow", "DONE": "bold white"}
LEGACY_ROUTER = {"laya": "Laya (laya_mlx, local MLX)", "arch": "Arch-Router-1.5B (katanemo, local)",
                 "fixed": "fixed policy (no model)", "random": "random policy (no model)"}
EXIT_REASON = {"StepIdle": "no model reply for too long", "TimeExceeded": "hit the step's time cap",
               "LimitsExceeded": "used all its turns", "RepeatedFormatError": "kept replying in the wrong format",
               "RuntimeError": "a model call kept failing"}
CMD_RE = re.compile(r"```mswea_bash_command\s*(.*?)(?:```|$)", re.S)


# ---------- pure renderers (tested without a terminal) ----------

def _k(n) -> str:
    n = float(n or 0)
    return f"{n / 1000:.1f}k" if n >= 1000 else f"{n:.0f}"


def _short(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _secs(ms) -> str:
    return f"{float(ms or 0) / 1000:.1f}s"


def _usage(e: dict) -> str:
    parts = [f"{_k(e.get('input_tokens'))} in", f"{_k(e.get('output_tokens'))} out"]
    if e.get("factory_credits"):
        parts.append(f"{_k(e['factory_credits'])} credits")
    if e.get("cost_usd"):
        parts.append(f"${e['cost_usd']:.4f}")
    return " · ".join(parts)


def _router_name(e: dict) -> str:
    return e.get("router") or LEGACY_ROUTER.get(str(e.get("source", "")).split(" ")[0], e.get("source", "?"))


def _tier_model(e: dict, events: list[dict]) -> str:
    """The model a route went to; older traces only logged the tier, so map it through the run's config."""
    if e.get("model"):
        return e["model"]
    tiers = next((c.get("tiers") for c in events if c.get("event") == "config"), None) or []
    tier = e.get("tier")
    return tiers[tier] if isinstance(tier, int) and tier < len(tiers) else f"tier {tier}"


def _line(t, role: str, who: str, what: str) -> Text:
    line = Text(f"{float(t or 0):7.1f}s ", style="dim")
    line.append(f"{role:<9}", style=ROLE_STYLE.get(role, ""))
    line.append(f"{who:<34} ", style="bold")
    line.append(what)
    return line


def activity_lines(events: list[dict]) -> list[Text]:
    """One line per thing that happened, naming the model or policy that did it."""
    out = []
    cfg = next((e for e in events if e.get("event") == "config"), {})
    for e in events:
        ev, t = e.get("event"), e.get("t")
        if ev == "llm_call" and e.get("role") == "planner":
            out.append(_line(t, "PLANNER", e.get("model", "?"), f"{_secs(e.get('ms'))}  {_usage(e)}"))
        elif ev == "plan":
            steps = e.get("steps", [])
            who = e.get("planner_model") or cfg.get("planner_model", "planner")
            out.append(_line(t, "PLANNER", who, f"plan: {len(steps)} steps"
                             + (" (fallback single step)" if e.get("fallback") else "")))
            for s in steps:
                out.append(Text(f"{'':17}{s['id']}: {_short(s['goal'], 95)}", style="dim"))
        elif ev == "route":
            conf = f" conf {e['confidence']:.2f}" if e.get("confidence") is not None else ""
            score = f" score {e['score']:.2f}" if e.get("score") is not None else ""
            out.append(_line(t, "ROUTER", _router_name(e)[:34],
                             f"{e.get('step')} → tier {e.get('tier')} {_tier_model(e, events)}{score}{conf} "
                             f"({float(e.get('ms') or 0):.0f} ms)"))
        elif ev == "llm_call" and e.get("role") == "worker":
            m = CMD_RE.search(e.get("reply", ""))
            said = f"$ {m.group(1).strip()}" if m else e.get("reply", "")
            out.append(_line(t, "WORKER", e.get("model", "?"),
                             f"{e.get('step')}  {_secs(e.get('ms'))}  {_usage(e)}  {said[:140]}"))
        elif ev == "llm_call_failed":
            what = {"timeout": "no reply", "provider_unavailable": "provider unreachable",
                    "transient": "quick failure", "error": "failed"}.get(e.get("kind"), e.get("kind"))
            then = "retrying" if e.get("will_retry") else "giving up on this call"
            line = _line(t, e.get("role", "?").upper(), e.get("model", "?"),
                         f"{e.get('step', '')}  {_secs(e.get('ms'))}  ")
            line.append(f"CALL FAILED: {what}, {then}", style="bold red")
            out.append(line)
        elif ev == "attempt":
            check = e.get("check") or ""
            how = f"check `{check[:60]}`" if check else "no check, trusts the worker's submit"
            verdict = Text("PASS", style="bold green") if e.get("passed") else Text("FAIL", style="bold red")
            line = _line(t, "VERIFIER", "task container",
                         f"{e.get('step')} attempt {e.get('attempt')} by {e.get('model')}: {how} → ")
            line.append(verdict)
            line.append(f"  ({e.get('n_calls')} worker calls, {e.get('exit_status')})", style="dim")
            out.append(line)
        elif ev == "escalate":
            out.append(_line(t, "ESCALATE", "orchestrator",
                             f"{e.get('step')} tier {e.get('from_tier')} → {e.get('to_tier')} (counted as a router error)"))
        elif ev == "done":
            out.append(_line(t, "DONE", "orchestrator", f"escalations {e.get('n_escalations')}, "
                             f"failed steps {e.get('n_failed_steps')}"))
    return out


def roles_text(events: list[dict]) -> Text:
    """Who held each role in this run, with what they did."""
    cfg = next((e for e in events if e.get("event") == "config"), {})
    calls = [e for e in events if e.get("event") == "llm_call"]
    routes = [e for e in events if e.get("event") == "route"]
    attempts = [e for e in events if e.get("event") == "attempt"]
    text = Text()

    def row(role, body):
        text.append(f"{role:<9}", style=ROLE_STYLE.get(role, "bold"))
        text.append(body + "\n")

    planner = [c for c in calls if c.get("role") == "planner"]
    pm = (planner[0]["model"] if planner else
          next((e.get("planner_model") for e in events if e.get("event") == "plan" and e.get("planner_model")),
               cfg.get("planner_model", "?")))
    row("PLANNER", f"{pm}" + (f"  {len(planner)} call(s), {_secs(sum(c.get('ms', 0) for c in planner))}" if planner else ""))
    routers: dict[str, int] = {}
    for r in routes:
        routers[_router_name(r)] = routers.get(_router_name(r), 0) + 1
    policy = f"policy {cfg.get('policy', '?')}"
    if cfg.get("policy") == "cascade":
        policy += f", Arch-Router below conf {cfg.get('confidence_threshold')}"
    row("ROUTER", (", ".join(f"{k} ×{v}" for k, v in routers.items()) or "(no step routed yet)") + f"  [{policy}]")
    by_model: dict[str, list] = {}
    for c in calls:
        if c.get("role") == "worker":
            by_model.setdefault(c["model"], []).append(c)
    if by_model:
        row("WORKER", "; ".join(
            f"{m} ×{len(cs)} ({_secs(sum(c.get('ms', 0) for c in cs))}, "
            f"{_usage({k: sum(c.get(k) or 0 for c in cs) for k in ('input_tokens', 'output_tokens', 'factory_credits', 'cost_usd')})})"
            for m, cs in by_model.items()))
    else:
        used = {a.get("model") for a in attempts}
        row("WORKER", ", ".join(sorted(used)) or "(no worker call yet)")
    passed = sum(1 for a in attempts if a.get("passed"))
    row("VERIFIER", f"step checks in the task container: {passed} pass, {len(attempts) - passed} fail")
    tiers = cfg.get("tiers") or []
    used_models = {r.get("model") for r in routes} | {a.get("model") for a in attempts}
    if tiers:
        text.append("TIERS    ", style="bold")
        for i, m in enumerate(tiers):
            style = "bold" if m in used_models else "dim"
            text.append(f"{i}:{m}" + (" (escalation)" if i == len(tiers) - 1 else ""), style=style)
            text.append("   ")
    return text


def totals(events: list[dict]) -> dict:
    calls = [e for e in events if e.get("event") == "llm_call"]
    keys = ("input_tokens", "output_tokens", "factory_credits", "cost_usd", "ms")
    return {"calls": len(calls), **{k: sum(c.get(k) or 0 for c in calls) for k in keys}}


def step_rows(events: list[dict]) -> list[tuple]:
    plan = next((e for e in events if e.get("event") == "plan"), None)
    if not plan:
        return []
    rows = []
    for s in plan.get("steps", []):
        route = next((e for e in events if e.get("event") == "route" and e.get("step") == s["id"]), None)
        atts = [e for e in events if e.get("event") == "attempt" and e.get("step") == s["id"]]
        if atts and atts[-1].get("passed"):
            status = "PASS"
        elif atts and (len(atts) == 2 or not any(e.get("event") == "escalate" and e.get("step") == s["id"]
                                                 for e in events)):
            status = "FAIL"
        elif route:
            status = "running"
        else:
            status = "waiting"
        calls = sum(1 for e in events if e.get("event") == "llm_call" and e.get("step") == s["id"])
        rows.append((s["id"], _short(s["goal"], 38), _router_name(route).split(" (")[0][:16] if route else "-",
                     _tier_model(route, events) if route else "-",
                     " → ".join(a.get("model", "?").split("/")[-1] for a in atts) or "-", status,
                     str(calls or sum(a.get("n_calls") or 0 for a in atts))))
    return rows


def worker_lines(messages: list[dict], max_obs: int = 1500) -> list[Text]:
    out = []
    for m in messages[2:]:  # skip system + task prompt
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "assistant":
            out.append(Text("model ▸ ", style="bold blue") + Text(content.strip()))
        elif role == "exit":
            out.append(Text(f"exit ▸ {m.get('extra', {}).get('exit_status', '')} {content[:300]}", style="bold"))
        else:
            out.append(Text("shell ◂ ", style="bold green") + Text(content.strip()[:max_obs], style="dim"))
        out.append(Text(""))
    return out


# ---------- plain-language story (the default "Who did what" view) ----------

PRETTY = {"gpt": "GPT", "glm": "GLM", "deepseek": "DeepSeek", "kimi": "Kimi", "claude": "Claude",
          "sol": "Sol", "flash": "Flash", "haiku": "Haiku", "sonnet": "Sonnet", "opus": "Opus"}
BACKEND = {"droid": "via Droid", "claude": "via Claude Code"}
SUBMIT_MARK = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
OBS_RE = re.compile(r"<returncode>(-?\d+)</returncode>\s*(?:<output>\s*(.*?)\s*(?:</output>|$))?", re.S)


def pretty_model(model_id: str) -> str:
    """droid/deepseek-v4.1-flash -> 'DeepSeek V4.1 Flash (via Droid)'."""
    backend, _, name = model_id.partition("/") if "/" in model_id else ("", "", model_id)
    name = name.removeprefix("claude-") if backend == "claude" else name
    words = []
    for w in re.split(r"[-_]", name):
        if re.fullmatch(r"v?\d+(\.\d+)*", w):
            words.append(w.upper() if w.startswith("v") else w)
        else:
            words.append(PRETTY.get(w, w.capitalize()))
    text = " ".join(words)
    text = re.sub(r"(\d) (\d)", r"\1.\2", text)  # claude-opus-5-5 -> Opus 5.5
    text = re.sub(r"\bGPT (\d)", r"GPT-\1", text)
    if backend == "claude":
        text = "Claude " + text
    return f"{text} ({BACKEND.get(backend, backend)})" if backend else text


def difficulty(score) -> str:
    if score is None:
        return ""
    return "easy" if score < 0.67 else "medium" if score < 1.33 else "hard"


def describe_command(cmd: str) -> str:
    """A few words on what a shell command does, for people skimming the run."""
    if SUBMIT_MARK in cmd:
        return "declared the step done"
    parts = [p.strip() for p in cmd.strip().split("&&")]
    core = next((p for p in parts if not p.startswith("cd ")), parts[-1])  # first real action after any cd
    first = core.split("|")[0].strip()
    tok = first.split()
    verb = tok[0] if tok else ""
    args = [t for t in tok[1:] if not t.startswith("-")]
    target = " ".join(args[:3])
    table = {
        "ls": "listed files", "tree": "listed files", "find": "searched for files",
        "cat": f"read {target}", "head": f"read the start of {target}", "tail": f"read the end of {target}",
        "less": f"read {target}", "wc": f"counted lines in {target}", "grep": "searched text", "rg": "searched text",
        "awk": f"extracted columns from {args[-1] if args else 'data'}", "sort": "sorted data", "uniq": "counted repeats",
        "xxd": f"hex-dumped {target}", "od": f"hex-dumped {args[-1] if args else ''}", "hexdump": f"hex-dumped {target}",
        "file": f"checked file types of {target}", "strings": f"pulled text out of {target}",
        "python3": "ran a Python script", "python": "ran a Python script", "node": "ran a JS script",
        "pip": "installed Python packages", "pip3": "installed Python packages", "apt-get": "installed system packages",
        "mkdir": "made a folder", "cp": "copied files", "mv": "moved files", "rm": "deleted files",
        "echo": "wrote output", "printf": "wrote output", "sed": "edited text", "git": f"used git ({target})",
        "curl": "fetched a URL", "wget": "fetched a URL", "make": "ran make", "gcc": "compiled C", "cargo": "ran cargo",
        "npm": "ran npm", "pytest": "ran tests", "jq": "queried JSON",
        "for": "looped over items", "while": "ran a loop", "bash": "ran a shell script", "sh": "ran a shell script",
        "test": "checked a condition", "diff": "compared files", "tar": "unpacked an archive", "unzip": "unpacked an archive",
    }
    desc = table.get(verb, f"ran {verb}" if verb else "ran a command")
    if ">" in core and verb in ("echo", "printf", "cat", "python3", "python"):
        dest = core.rsplit(">", 1)[-1].strip().split()[0] if core.rsplit(">", 1)[-1].strip() else ""
        desc = f"wrote {dest}" if dest else desc
    return desc


def split_reply(text: str) -> tuple[str, str]:
    """(what the model says it is doing, the shell command) from one worker reply."""
    m = CMD_RE.search(text or "")
    cmd = m.group(1).strip() if m else ""
    thought = (text or "")[: m.start()] if m else (text or "")
    thought = " ".join(thought.replace("THOUGHT:", "").split())
    return thought, cmd


def summarize_output(obs: str) -> tuple[bool, str]:
    m = OBS_RE.search(obs or "")
    if not m:
        return True, _short(obs, 110)
    code, body = int(m.group(1)), (m.group(2) or "")
    lines = [l for l in body.splitlines() if l.strip() and not re.match(r"total \d+$", l.strip())]
    first = _short(lines[0], 100) if lines else "(no output)"
    trimmed = " · long output, trimmed" if "characters elided" in body else ""
    head = f"ok · {len(lines)} lines{trimmed}" if code == 0 else f"failed (exit {code})"
    return code == 0, f"{head} · {first}"


def _attempt_files(workers: dict, step: str) -> list[tuple[int, list]]:
    return sorted(((int(k.rsplit("_a", 1)[1]), v) for k, v in workers.items()
                   if k.startswith(f"worker_{step}_a")), key=lambda kv: kv[0])


def story_lines(events: list[dict], workers: dict[str, list]) -> list[Text]:
    """What each agent did, in words, grouped by step. `workers` maps 'worker_s1_a1' -> messages."""
    out: list[Text] = []
    cfg = next((e for e in events if e.get("event") == "config"), {})
    tiers = cfg.get("tiers") or []
    plan = next((e for e in events if e.get("event") == "plan"), None)
    pcalls = [e for e in events if e.get("event") == "llm_call" and e.get("role") == "planner"]

    def head(role: str, who: str, extra: str = "") -> Text:
        t = Text(f"{role} ", style=ROLE_STYLE.get(role, "bold"))
        t.append(who, style="bold")
        if extra:
            t.append(f"  {extra}", style="dim")
        return t

    if plan:
        who = pretty_model(plan.get("planner_model") or cfg.get("planner_model", "planner"))
        took = f"{sum(c.get('ms', 0) for c in pcalls) / 1000:.0f}s" if pcalls else ""
        out.append(head("PLANNER", who, took))
        steps = plan.get("steps", [])
        lead = "Could not plan, so it treats the whole task as one step:" if plan.get("fallback") else \
            f"Split the task into {len(steps)} step{'s' if len(steps) != 1 else ''}:"
        out.append(Text(f"  {lead}"))
        for s in steps:
            after = f" (after {', '.join(s['depends_on'])})" if s.get("depends_on") else ""
            out.append(Text(f"    {s['id']}{after}: {_short(s['goal'], 150)}"))
            out.append(Text(f"        done when: {_short(s['check'], 90) if s.get('check') else 'the worker says it is done'}",
                            style="dim"))
        out.append(Text(""))
    elif cfg:
        out.append(head("PLANNER", pretty_model(cfg.get("planner_model", "?")), "planning ..."))

    for s in (plan or {}).get("steps", []):
        sid = s["id"]
        route = next((e for e in events if e.get("event") == "route" and e.get("step") == sid), None)
        if not route:
            out.append(Text(f"STEP {sid}  waiting for: {', '.join(s.get('depends_on') or []) or 'its turn'}",
                            style="dim"))
            continue
        out.append(Text(f"STEP {sid}  {_short(s['goal'], 120)}", style="bold underline"))
        model = _tier_model(route, events)
        router = _router_name(route).split(" (")[0]
        if route.get("score") is not None:
            sure = f", {route['confidence'] * 100:.0f}% sure" if route.get("confidence") is not None else ""
            judged = f"judged it {difficulty(route['score'])} ({route['score']:.2f} of 2{sure})"
        else:
            judged = f"used {_router_name(route)}"
        t = head("ROUTER", router)
        t.append(f"  {judged} → sent it to ")
        t.append(pretty_model(model), style="bold")
        t.append(f" (tier {route.get('tier')} of {len(tiers) - 1})" if tiers else "", style="dim")
        out.append(t)
        calls = [e for e in events if e.get("event") == "llm_call" and e.get("role") == "worker" and e.get("step") == sid]
        call_i = 0
        attempts = {e.get("attempt"): e for e in events if e.get("event") == "attempt" and e.get("step") == sid}
        esc = next((e for e in events if e.get("event") == "escalate" and e.get("step") == sid), None)
        esc_model = tiers[esc["to_tier"]] if esc and tiers and esc.get("to_tier", 99) < len(tiers) else ""
        for n, msgs in _attempt_files(workers, sid):
            if n > 1 and esc:
                out.append(head("ESCALATE", "orchestrator", f"first attempt did not pass, retrying one tier up on "
                                f"{pretty_model(esc_model) if esc_model else 'the next tier'}"))
            amodel = attempts.get(n, {}).get("model") or (model if n == 1 else esc_model)
            out.append(head("WORKER", pretty_model(amodel) if amodel else "worker",
                            f"attempt {n}, tool: bash in the task container"))
            turn = 0
            for i, m in enumerate(msgs[2:], start=2):
                if m.get("role") != "assistant":
                    continue
                turn += 1
                thought, cmd = split_reply(str(m.get("content") or ""))
                secs = f"{calls[call_i]['ms'] / 1000:.0f}s" if call_i < len(calls) else ""
                call_i += 1
                line = Text(f"  {turn:>2}. ", style="dim")
                line.append(describe_command(cmd) if cmd else "replied without a command", style="bold")
                if secs:
                    line.append(f"  ({secs})", style="dim")
                out.append(line)
                if thought:
                    out.append(Text(f"      thinking: {_short(thought, 170)}", style="italic"))
                if cmd and SUBMIT_MARK not in cmd:
                    out.append(Text(f"      $ {_short(cmd, 170)}", style="cyan"))
                nxt = msgs[i + 1] if i + 1 < len(msgs) else None
                if nxt and nxt.get("role") == "exit":
                    out.append(Text(f"      → {_short(nxt.get('content'), 170)}", style="green"))
                elif nxt and nxt.get("role") != "assistant":
                    ok, summary = summarize_output(str(nxt.get("content") or ""))
                    out.append(Text(f"      → {summary}", style="green" if ok else "red"))
                elif not nxt:
                    out.append(Text("      … running", style="yellow"))
            a = attempts.get(n)
            if a:
                v = head("VERIFIER", "task container")
                how = f"ran `{_short(a['check'], 60)}`" if a.get("check") else "no check for this step, accepted the worker's done"
                v.append(f"  {how} → ")
                v.append("passed ✔" if a.get("passed") else "failed ✘", style="bold green" if a.get("passed") else "bold red")
                if a.get("exit_status") not in (None, "Submitted"):
                    v.append(f"  (worker stopped: {a['exit_status']})", style="dim")
                out.append(v)
        if esc and not _attempt_files(workers, sid)[1:]:
            out.append(head("ESCALATE", "orchestrator", f"retrying one tier up on {pretty_model(esc_model) or 'the next tier'}"))
        out.append(Text(""))
    done = next((e for e in events if e.get("event") == "done"), None)
    if done:
        ok = done.get("n_failed_steps") == 0
        out.append(head("DONE", "orchestrator",
                        "all steps passed" if ok else f"{done.get('n_failed_steps')} step(s) failed"
                        + (f", {done.get('n_escalations')} escalation(s)" if done.get("n_escalations") else "")))
    return out

# ---------- Overview tab: a plain tree of what happened, no numbers ----------

def first_sentence(text: str, n: int = 100) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    guarded = re.sub(r"\b(e\.g|i\.e|etc|vs)\.", lambda m: m.group(0).replace(".", "\0"), text)
    cut = re.split(r"(?<=[.!?])\s", guarded, maxsplit=1)[0].rstrip(".").replace("\0", ".")
    return _short(cut, n)


def turn_summary(thought: str, cmd: str, ok: bool | None) -> Text:
    if cmd and SUBMIT_MARK in cmd:
        return Text("Said the step is finished", style="bold")
    action = describe_command(cmd) if cmd else "answered without running anything"
    action = action[0].upper() + action[1:]
    t = Text(action, style="bold" if ok is not False else "bold red")
    why = first_sentence(thought)
    if why:
        t.append(f" — {why}", style="")
    if ok is False:
        t.append("  (failed)", style="red")
    elif ok is None:
        t.append("  … working", style="yellow")
    return t


def _clock(sec: float) -> str:
    sec = max(int(sec), 0)
    return f"{sec // 3600}h {sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m {sec % 60:02d}s"


def step_states(events: list[dict], now_t: float) -> list[dict]:
    """Plain status of every planned step: waiting / working / retrying / done / failed, with its
    model, actions so far and timings. `now_t` is the current time on the trace clock (seconds)."""
    plan = next((e for e in events if e.get("event") == "plan"), None)
    if not plan:
        return []
    steps = plan["steps"]
    merged = next((e for e in events if e.get("event") == "merged"), None)
    if merged:  # a pure chain ran as one step
        steps = [{"id": merged["into"], "depends_on": [],
                  "goal": f"all {len(merged['parts'])} planned parts as one step: " + steps[0]["goal"]}]
    out = []
    for s in steps:
        sid = s["id"]
        mine = [e for e in events if e.get("step") == sid]
        route = next((e for e in mine if e.get("event") == "route"), None)
        attempts = [e for e in mine if e.get("event") == "attempt"]
        calls = [e for e in mine if e.get("event") == "llm_call"]
        escalations = [e for e in mine if e.get("event") == "escalate"]
        failures = [e for e in mine if e.get("event") == "llm_call_failed"]
        end = next((e for e in mine if e.get("event") == "step_end"), None)
        done = end["passed"] if end else any(a.get("passed") for a in attempts)
        over = bool(end) or done or (attempts and len(attempts) > len(escalations))
        state = ("done" if done else "failed" if over else "retrying" if escalations
                 else "working" if route else "waiting")
        model = (route or {}).get("model") or (attempts[-1].get("model") if attempts else None)
        if escalations:
            tiers = next((e.get("tiers") for e in events if e.get("event") == "config"), None) or []
            to = escalations[-1].get("to_tier")
            model = tiers[to] if isinstance(to, int) and to < len(tiers) else (calls[-1]["model"] if calls else model)
        # current attempt's calls only: actions since the last escalation
        since = escalations[-1]["t"] if escalations else (route or {}).get("t", 0)
        cur_calls = [c for c in calls if c["t"] >= since]
        started = (route or {}).get("t")
        ended = attempts[-1]["t"] if over and attempts else None
        last = max([e["t"] for e in mine] or [since])
        out.append({
            "id": sid, "goal": s["goal"], "state": state, "model": model,
            "depends_on": s.get("depends_on") or [],
            "actions": len(cur_calls) or (attempts[-1].get("n_calls") or 0 if over and attempts else 0),
            "attempt": len(escalations) + 1,
            "elapsed": ((ended if ended is not None else now_t) - started) if started is not None else 0,
            "attempt_elapsed": (now_t - since) if state in ("working", "retrying") else 0,
            "idle": (now_t - last) if state in ("working", "retrying") else 0,
            "failed_calls": len(failures),
            "has_check": bool((s.get("check") or "").strip()),
        })
    return out


STATE_LOOK = {"done": ("✓", "bold green", "done"), "failed": ("✗", "bold red", "failed"),
              "working": ("●", "bold yellow", "working"), "retrying": ("●", "bold yellow",
                                                                     "retrying with a stronger model"),
              "waiting": ("○", "dim", "waiting"), "stopped": ("■", "dim", "stopped mid-work")}


def trace_now(job, events: list[dict]) -> float:
    """Seconds on the trace clock right now (trace t=0 is when the harness started). For a finished run,
    the time of its last event."""
    import time as _time

    if not events or not job.agent_dir:
        return 0.0
    trace = job.agent_dir / "trace.jsonl"
    try:
        start = trace.stat().st_mtime - events[-1]["t"]
    except OSError:
        return events[-1]["t"]
    return (_time.time() - start) if job.status == "running" else events[-1]["t"]


def spend(events: list[dict], step_id: str | None = None) -> dict:
    """Model spend from the trace: list-price USD, Factory credits, input tokens and the cached share."""
    calls = [e for e in events if e.get("event") == "llm_call" and (step_id is None or e.get("step") == step_id)]
    inp = sum(e.get("input_tokens") or 0 for e in calls)
    return {"usd": sum(e.get("cost_usd") or 0 for e in calls), "credits": sum(e.get("factory_credits") or 0 for e in calls),
            "input": inp, "cached": sum(e.get("cached_tokens") or 0 for e in calls), "calls": len(calls)}


def spend_text(s: dict) -> str:
    parts = ([f"${s['usd']:.2f}"] if s["usd"] else []) + ([f"{s['credits']:,.0f} credits"] if s["credits"] else [])
    return " + ".join(parts) or "$0.00"


def progress_panel(events: list[dict], now_t: float, status: str = "running", reward=None) -> Text:
    """The top panel: what the run is doing right now, in plain words, with a progress bar."""
    cfg = next((e for e in events if e.get("event") == "config"), {})
    steps = step_states(events, now_t)
    if status != "running":  # a step that was mid-work when the run ended did not finish
        for st in steps:
            if st["state"] in ("working", "retrying"):
                st["state"] = "stopped"
    t = Text()
    if not events:
        t.append("Starting up: building the task's container and loading the router. This takes a minute or two.",
                 style="dim")
        return t
    if not steps:
        planner = pretty_model(cfg.get("planner_model", "the planner"))
        t.append(f"Planning: {planner} is reading the task and splitting it into steps "
                 f"({_clock(now_t)} so far).", style="yellow")
        return t
    n_done = sum(s["state"] == "done" for s in steps)
    n_fail = sum(s["state"] == "failed" for s in steps)
    width = 24
    filled = round(width * (n_done + n_fail) / len(steps))
    t.append("Progress  ", style="bold")
    t.append("█" * filled, style="green")
    t.append("░" * (width - filled), style="dim")
    t.append(f"  {n_done} of {len(steps)} steps done" + (f", {n_fail} failed" if n_fail else ""))
    t.append(f"   ·   {'running for' if status == 'running' else 'ran for'} {_clock(now_t)}\n", style="dim")
    total = spend(events)
    caps = [f"${cfg['max_run_cost_usd']:.2f}" if cfg.get("max_run_cost_usd") else "",
            f"{cfg['max_run_credits']:,.0f} credits" if cfg.get("max_run_credits") else ""]
    cap = " / ".join(c for c in caps if c)
    line = f"Spent     {spend_text(total)}" + (f" of {cap} cap" if cap else "")
    if total["input"]:
        line += f"   ·   {100 * total['cached'] / total['input']:.0f}% of input from cache"
    t.append(line + "\n", style="dim")
    team = {k: sum(1 for e in events if e.get("event") == k) for k in ("fork", "triage", "rerun", "stop")}
    updates = sum(1 for e in events if e.get("event") == "llm_call" and e.get("team_update"))
    reviews = sum(1 for e in events if e.get("event") == "llm_call" and e.get("purpose") == "review")
    bits = [f"{updates} team update(s)" if updates else "", f"{team['fork']} step(s) continued a teammate's memory"
            if team["fork"] else "", f"{reviews} value review(s)" if reviews else "",
            f"{team['triage']} error-handler call(s)" if team["triage"] else "",
            f"{team['stop']} stop(s) to save budget" if team["stop"] else ""]
    acc = next((e for e in events if e.get("event") == "acceptance"), None)
    if any(e.get("event") == "acceptance_planned" for e in events):
        bits.append("acceptance tests " + ("being written" if not acc else "ready" if acc.get("ready") else "FAILED to write"))
    reopens = sum(1 for e in events if e.get("event") == "reopen")
    if reopens:
        bits.append(f"{reopens} step(s) re-opened by acceptance tests")
    if any(bits):
        t.append("Team      " + " · ".join(b for b in bits if b) + "\n", style="dim")
    turn_cap = cfg.get("worker_step_limit")
    time_cap = cfg.get("step_wall_time_sec")
    idle_cap = cfg.get("step_idle_sec")
    for i, s in enumerate(steps, 1):
        icon, style, word = STATE_LOOK[s["state"]]
        t.append(f"  {icon} Step {i} ({s['id']})  ", style=style)
        t.append(_short(first_sentence(s["goal"], 70), 70))
        t.append("\n")
        who = pretty_model(s["model"]) if s["model"] else ""
        if s["state"] == "waiting":
            deps = [str(next(j for j, x in enumerate(steps, 1) if x["id"] == d)) for d in s["depends_on"]
                    if any(x["id"] == d for x in steps)]
            which = (f"step {deps[0]}" if len(deps) == 1 else
                     "steps " + ", ".join(deps[:-1]) + " and " + deps[-1]) if deps else ""
            t.append(f"           waits for {which} to finish\n" if deps else
                     "           waiting for a free worker slot\n", style="dim")
        elif s["state"] in ("working", "retrying"):
            line = f"           {word}: {who} · action {s['actions'] + 1}"
            line += f" of {turn_cap}" if turn_cap else ""
            line += f" · {_clock(s['attempt_elapsed'])}" + (f" of {_clock(time_cap)} allowed" if time_cap else "")
            t.append(line + "\n", style="yellow")
            wait = Text(f"           waiting for {who.split(' (')[0]}'s next move: {_clock(s['idle'])}",
                        style="dim")
            if idle_cap and s["idle"] > idle_cap * 0.5:
                wait.stylize("bold red")
                wait.append(f"  (gives up at {_clock(idle_cap)} with no reply)", style="red")
            t.append_text(wait)
            if s["failed_calls"]:
                t.append(f"  · {s['failed_calls']} failed model call(s)", style="red")
            t.append("\n")
        elif s["state"] == "stopped":
            t.append(f"           {word}: {who} had taken {s['actions']} actions\n", style="dim")
        else:
            line = (f"           {word} by {who} in {_clock(s['elapsed'])} · {s['actions']} actions"
                    f" · {spend_text(spend(events, s['id']))}")
            if s["attempt"] > 1:
                line += f" · needed {s['attempt'] - 1} escalation(s)"
            t.append(line + "\n", style="dim")
    if status != "running":
        if reward is not None:
            verdict = "passed" if reward == 1 else "did not pass"
            t.append(f"\nTerminal-Bench result: {verdict} (score {reward})", style="bold")
            if reward == 1 and n_fail:
                t.append(f"\n(The task passed although {n_fail} step(s) failed their own check: the planner's "
                         "check was stricter than, or different from, the task's tests.)", style="dim")
        else:
            t.append(f"\nRun ended: {status}", style="bold")
    return t


def overview(events: list[dict], workers: dict[str, list], result: dict | None = None) -> list[tuple]:
    """Tree rows as (key, label, children); children has the same shape. Pure, for the Tree widget and tests."""
    cfg = next((e for e in events if e.get("event") == "config"), {})
    tiers = cfg.get("tiers") or []
    plan = next((e for e in events if e.get("event") == "plan"), None)
    rows = []
    if not plan:
        who = pretty_model(cfg.get("planner_model", "")) if cfg else "the planner"
        rows.append(("plan", Text(f"Planner: {who} is reading the task and making a plan …", style="yellow"), []))
        return rows
    steps = plan.get("steps", [])
    planner = pretty_model(plan.get("planner_model") or cfg.get("planner_model", "?"))
    rows.append(("plan", Text.assemble(("Planner: ", "bold magenta"), (planner, "bold"),
                                       f" split the task into {len(steps)} step{'s' if len(steps) != 1 else ''}"), []))
    for s in steps:
        sid = s["id"]
        route = next((e for e in events if e.get("event") == "route" and e.get("step") == sid), None)
        atts = {e.get("attempt"): e for e in events if e.get("event") == "attempt" and e.get("step") == sid}
        last = atts[max(atts)] if atts else None
        esc = next((e for e in events if e.get("event") == "escalate" and e.get("step") == sid), None)
        finished = last and (last.get("passed") or not esc or len(atts) == 2)
        if last and last.get("passed"):
            icon, style = "✔", "bold green"
        elif finished:
            icon, style = "✘", "bold red"
        elif route:
            icon, style = "●", "bold yellow"
        else:
            icon, style = "○", "dim"
        label = Text(f"{icon} ", style=style)
        label.append(_short(s["goal"], 95), style="bold" if route else "dim")
        children = []
        if not route:
            wait = ", ".join(s.get("depends_on") or [])
            children.append((f"{sid}/wait", Text(f"Waiting for {wait or 'its turn'}", style="dim"), []))
            rows.append((sid, label, children))
            continue
        router = _router_name(route).split(" (")[0]
        target = pretty_model(_tier_model(route, events))
        rated = f" rated it {difficulty(route['score'])}" if route.get("score") is not None else ""
        children.append((f"{sid}/route", Text.assemble(("Router: ", "bold cyan"), (router, "bold"),
                                                       f"{rated} and gave it to ", (target, "bold")), []))
        esc_model = tiers[esc["to_tier"]] if esc and tiers and esc.get("to_tier", 99) < len(tiers) else ""
        handoff = next((e for e in events if e.get("event") == "handoff" and e.get("step") == sid), None)
        for n, msgs in _attempt_files(workers, sid):
            if n > 1 and esc:
                children.append((f"{sid}/esc", Text.assemble(
                    ("Escalated: ", "bold yellow"), f"the first attempt did not pass, so the step goes to ",
                    (pretty_model(esc_model) if esc_model else "the next tier", "bold"),
                    " with the first attempt's notes" if handoff else " (starting over)"), []))
            model = pretty_model((atts.get(n) or {}).get("model") or
                                 (_tier_model(route, events) if n == 1 else esc_model) or "?")
            turns = []
            assistant_idx = [i for i, m in enumerate(msgs) if i >= 2 and m.get("role") == "assistant"]
            for k, i in enumerate(assistant_idx):
                thought, cmd = split_reply(str(msgs[i].get("content") or ""))
                nxt = msgs[i + 1] if i + 1 < len(msgs) else None
                ok = None if nxt is None else (True if nxt.get("role") == "exit" else summarize_output(str(nxt.get("content") or ""))[0])
                turns.append((f"{sid}/a{n}/t{k}", turn_summary(thought, cmd, ok), []))
            head = Text.assemble(("Worker: ", "bold blue"), (model, "bold"),
                                 " (second try, one tier up)" if n > 1 else "")
            children.append((f"{sid}/a{n}", head, turns or [(f"{sid}/a{n}/start", Text("Starting …", style="yellow"), [])]))
            a = atts.get(n)
            if a:
                if a.get("check"):
                    verdict = "passed" if a.get("passed") else "failed"
                    text = Text.assemble(("Check: ", "bold green"), f"the step's test {verdict}")
                else:
                    text = Text.assemble(("Check: ", "bold green"),
                                         "no test for this step, so the worker's word was accepted"
                                         if a.get("passed") else "no test, and the worker did not finish")
                if a.get("exit_status") not in (None, "Submitted"):
                    text.append(f" (worker stopped: {EXIT_REASON.get(a['exit_status'], a['exit_status'])})", style="dim")
                children.append((f"{sid}/a{n}/check", text, []))
        if esc and not any(k == f"{sid}/esc" for k, _, _ in children):
            children.append((f"{sid}/esc", Text.assemble(("Escalated: ", "bold yellow"), "to ",
                                                         (pretty_model(esc_model) or "the next tier", "bold")), []))
        rows.append((sid, label, children))
    done = next((e for e in events if e.get("event") == "done"), None)
    if result is not None:
        reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if reward is not None:
            rows.append(("result", Text.assemble(("Result: ", "bold"),
                                                 ("task solved ✔" if reward == 1 else "task not solved ✘",
                                                  "bold green" if reward == 1 else "bold red"),
                                                 " (Terminal-Bench's own tests)"), []))
    elif done:
        rows.append(("done", Text("All steps finished; Terminal-Bench is grading the result …", style="yellow"), []))
    return rows

# ---------- screens ----------

class NewRunScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss_none", "Cancel")]

    def compose(self) -> ComposeResult:
        configs = sorted(p.name for p in (ROOT / "configs").glob("phase2-*.json"))
        default_cfg = "phase2-droid.json" if "phase2-droid.json" in configs else configs[0]
        with VerticalScroll(id="dialog"):
            yield Label("New run", id="title")
            yield Label("Task")
            yield Select(J.TASKS, value=J.SMOKE_TASK, allow_blank=False, id="task")
            yield Label("Backend config (tiers, planner, prices)")
            yield Select([(c, f"configs/{c}") for c in configs], value=f"configs/{default_cfg}",
                         allow_blank=False, id="config")
            yield Label("Routing policy")
            yield Select([(p, p) for p in J.POLICIES] + [(f"fixed:{i}", f"fixed:{i}") for i in range(4)],
                         value="laya", allow_blank=False, id="policy")
            with Horizontal(id="caps"):
                for key, val, hint in (("max_plan_steps", "5", "max steps"), ("worker_step_limit", "25", "turns/step"),
                                       ("step_idle_sec", "600", "stop step after N sec idle")):
                    with Vertical():
                        yield Label(hint)
                        yield Input(val, type="integer", id=key)
            yield Label("Job name")
            yield Input(id="job_name")
            yield Static(id="preview")
            with Horizontal(id="buttons"):
                yield Button("Start run", variant="success", id="start")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._update()

    def _values(self) -> dict:
        caps = {k: self.query_one(f"#{k}", Input).value.strip()
                for k in ("max_plan_steps", "worker_step_limit", "step_idle_sec")}
        return {"task": self.query_one("#task", Select).value, "config": self.query_one("#config", Select).value,
                "policy": self.query_one("#policy", Select).value, "caps": caps,
                "job_name": self.query_one("#job_name", Input).value.strip()}

    def _default_name(self) -> str:
        v = self._values()
        task = str(v["task"]).split("/")[-1]
        backend = Path(str(v["config"])).stem.removeprefix("phase2-")
        return f"{task}-{backend}-{time.strftime('%m%d-%H%M%S')}"

    def _update(self) -> None:
        name = self.query_one("#job_name", Input)
        if not name.value or getattr(self, "_auto_name", None) == name.value:
            self._auto_name = name.value = self._default_name()
        v = self._values()
        cmd = J.build_command(v["task"], v["config"], v["policy"], v["caps"], v["job_name"])
        self.query_one("#preview", Static).update(Text(" ".join(cmd[2:]).replace(str(ROOT) + "/", ""), style="dim"))

    def on_select_changed(self, event: Select.Changed) -> None:
        self._update()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "job_name":
            self._update()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            v = self._values()
            if not v["job_name"]:
                self.notify("Job name is empty", severity="error")
                return
            self.dismiss(v)
        else:
            self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen):
    BINDINGS = [Binding("y", "yes", "Yes"), Binding("n,escape", "no", "No")]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Label(self.message)
            with Horizontal(id="buttons"):
                yield Button("Yes, stop it  [y]", variant="error", id="yes")
                yield Button("No  [n]", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


# ---------- app ----------

class HarnessTUI(App):
    TITLE = "MLR570 routed harness"
    CSS = """
    #sidebar { width: 38; border-right: solid $panel-lighten-2; padding: 0 1; }
    #sidebar Button { width: 100%; margin: 1 0; }
    #jobs { height: 1fr; }
    #main { padding: 0 1; }
    #meta { height: auto; padding: 1 0 0 0; }
    #progress { height: auto; border: round $panel-lighten-2; padding: 0 1; margin: 1 0 0 0; }
    #roles { height: auto; padding: 1 1; }
    TabbedContent { height: 1fr; }
    RichLog { height: 1fr; }
    NewRunScreen, ConfirmScreen { align: center middle; }
    #dialog { width: 90; height: auto; max-height: 90%; border: thick $accent; background: $surface; padding: 1 2; }
    #confirm { width: 70; height: auto; border: thick $error; background: $surface; padding: 1 2; }
    #title { text-style: bold; margin-bottom: 1; }
    #caps { height: auto; }
    #caps Vertical { height: auto; width: 1fr; }
    #buttons { height: auto; margin-top: 1; }
    #buttons Button { margin-right: 2; }
    #preview { margin-top: 1; }
    """
    BINDINGS = [Binding("n", "new_run", "New run"), Binding("s", "stop_run", "Stop run"),
                Binding("r", "refresh", "Refresh"), Binding("q", "quit", "Quit")]

    def __init__(self, root: Path = ROOT, launcher=J.start_run, stopper=J.stop_run, poll_sec: float = 2.0):
        super().__init__()
        self.root, self.launcher, self.stopper, self.poll_sec = root, launcher, stopper, poll_sec
        self.jobs: list[J.Job] = []
        self.selected: str | None = None
        self._list_sig = None
        self._view_sig = None
        self._collapsed: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Button("+ New run  [n]", id="new", variant="primary")
                yield ListView(id="jobs")
            with Vertical(id="main"):
                yield Static(id="meta")
                yield Static(id="progress")
                with TabbedContent(initial="tab-overview"):
                    with TabPane("Overview", id="tab-overview"):
                        yield Tree("run", id="overview")
                    with TabPane("Who did what", id="tab-activity"):
                        yield RichLog(id="story", wrap=True)
                    with TabPane("Steps", id="tab-steps"):
                        yield DataTable(id="steps", zebra_stripes=True, cursor_type="row")
                    with TabPane("Worker turns", id="tab-worker"):
                        yield RichLog(id="worker", wrap=True)
                    with TabPane("Details", id="tab-details"):
                        yield Static(id="roles")
                    with TabPane("Raw events", id="tab-raw"):
                        yield RichLog(id="feed", wrap=True)
                    with TabPane("Harbor log", id="tab-log"):
                        yield RichLog(id="log", wrap=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#steps", DataTable).add_columns("step", "goal", "routed by", "routed to",
                                                        "attempts", "status", "calls")
        await self.refresh_all()
        self.set_interval(self.poll_sec, self.refresh_all)

    # --- data refresh ---

    async def refresh_all(self) -> None:
        self.jobs = J.list_jobs(self.root / "jobs")
        sig = [(j.name, j.status) for j in self.jobs]
        if sig != self._list_sig:
            self._list_sig = sig
            lv = self.query_one("#jobs", ListView)
            await lv.clear()
            items = []
            for j in self.jobs:
                label = Text(f"{STATUS_ICON.get(j.status, '?')} ", style=STATUS_STYLE.get(j.status, ""))
                label.append(j.name)
                label.append(f"\n  {j.status}" + (f" · reward {j.reward}" if j.reward is not None else ""), style="dim")
                items.append(ListItem(Label(label)))
            await lv.extend(items)
            if self.selected is None and self.jobs:
                self.selected = self.jobs[0].name
            names = [j.name for j in self.jobs]
            if self.selected in names:
                lv.index = names.index(self.selected)
        self.render_job()

    def current(self) -> J.Job | None:
        return next((j for j in self.jobs if j.name == self.selected), None)

    def render_job(self, force: bool = False) -> None:
        job = self.current()
        meta = self.query_one("#meta", Static)
        if not job:
            meta.update(Text("No jobs yet. Press n to start a run.", style="dim"))
            return
        events = J.read_events(job.agent_dir)
        workers = J.worker_files(job.agent_dir)
        log = self.root / "jobs" / f"{job.name}.log"
        self.query_one("#progress", Static).update(
            progress_panel(events, trace_now(job, events), job.status, job.reward))
        sig = (job.name, job.status, len(events), workers[-1].stat().st_mtime if workers else 0,
               log.stat().st_size if log.exists() else 0)
        if sig == self._view_sig and not force:
            return
        self._view_sig = sig
        tot = totals(events)
        head = Text(job.name, style="bold")
        head.append(f"  {STATUS_ICON.get(job.status, '')} {job.status}", style=STATUS_STYLE.get(job.status, ""))
        if job.reward is not None:
            head.append(f"  reward {job.reward}", style="bold")
        elapsed = (events[-1]["t"] if events else 0)
        head.append(f"\ntask {job.task}   backend {Path(job.config).name or '?'}   policy {job.policy or '?'}"
                    f"   caps {job.caps or '-'}", style="dim")
        spent = (f"{_k(tot['factory_credits'])} Factory credits" if tot["factory_credits"] else
                 f"${tot['cost_usd']:.4f} at list price" if tot["cost_usd"] else "nothing yet")
        head.append(f"\nSpent so far: {spent} over {tot['calls']} model calls   (token details: Details tab)",
                    style="dim")
        if job.exception:
            head.append(f"\n{job.exception}", style="red")
        meta.update(head)
        roles = roles_text(events) if events else Text("nothing yet", style="dim")
        roles.append(f"\n\n{tot['calls']} model calls · {_k(tot['input_tokens'])} input tokens · "
                     f"{_k(tot['output_tokens'])} output tokens · {elapsed:.0f} s traced", style="dim")
        self.query_one("#roles", Static).update(roles)
        worker_msgs = {w.stem: J.read_worker(w) for w in workers}
        result = J._read_json(job.trial_dir / "result.json") if job.trial_dir else {}
        self._fill_overview(job, overview(events, worker_msgs, result or None))
        story = self.query_one("#story", RichLog)
        story.clear()
        for line in story_lines(events, worker_msgs):
            story.write(line)
        feed = self.query_one("#feed", RichLog)
        feed.clear()
        for line in activity_lines(events):
            feed.write(line)
        table = self.query_one("#steps", DataTable)
        table.clear()
        for row in step_rows(events):
            table.add_row(*row)
        wlog = self.query_one("#worker", RichLog)
        wlog.clear()
        if workers:
            wlog.write(Text(f"{workers[-1].stem}  (latest of {len(workers)})", style="bold"))
            for line in worker_lines(J.read_worker(workers[-1])):
                wlog.write(line)
        hlog = self.query_one("#log", RichLog)
        hlog.clear()
        if log.exists():
            for line in log.read_text(errors="replace").splitlines()[-200:]:
                hlog.write(Text(line))

    def _fill_overview(self, job: J.Job, rows: list[tuple]) -> None:
        tree = self.query_one("#overview", Tree)
        tree.show_root = True
        tree.clear()
        title = Text(f"{job.task.split('/')[-1] or job.name}  ", style="bold")
        title.append(f"{STATUS_ICON.get(job.status, '')} {job.status}", style=STATUS_STYLE.get(job.status, ""))
        tree.root.set_label(title)

        def add(parent, items):
            for key, label, children in items:
                if children:
                    node = parent.add(label, data=key, expand=key not in self._collapsed)
                    add(node, children)
                else:
                    parent.add_leaf(label, data=key)

        add(tree.root, rows)
        tree.root.expand()

    def on_tree_node_collapsed(self, event: Tree.NodeCollapsed) -> None:
        if event.node.data:
            self._collapsed.add(event.node.data)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        if event.node.data:
            self._collapsed.discard(event.node.data)

    # --- events and actions ---

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        lv = self.query_one("#jobs", ListView)
        if lv.index is not None and lv.index < len(self.jobs):
            name = self.jobs[lv.index].name
            if name != self.selected:
                self.selected = name
                self._collapsed.clear()
                self.render_job(force=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new":
            self.action_new_run()

    def action_new_run(self) -> None:
        def started(values):
            if not values:
                return
            try:
                caps = {k: v for k, v in values["caps"].items() if v}
                pid = self.launcher(self.root, values["task"], values["config"], values["policy"], caps,
                                    values["job_name"])
            except Exception as e:  # noqa: BLE001 - shown to the user
                self.notify(f"Could not start: {e}", severity="error")
                return
            self.selected = values["job_name"]
            self._list_sig = None
            self.notify(f"Started {values['job_name']} (pid {pid})")
            self.call_later(self.refresh_all)

        self.push_screen(NewRunScreen(), started)

    def action_stop_run(self) -> None:
        job = self.current()
        if not job or job.status != "running":
            self.notify("Select a running job to stop it", severity="warning")
            return

        def confirmed(yes):
            if yes:
                done = self.stopper(job)
                self.notify(f"Stopped {job.name}: " + (", ".join(done) or "nothing was running"))
                self._list_sig = None
                self.call_later(self.refresh_all)

        self.push_screen(ConfirmScreen(f"Stop {job.name}? Its container is removed too."), confirmed)

    async def action_refresh(self) -> None:
        self._list_sig = self._view_sig = None
        await self.refresh_all()


def main() -> None:
    HarnessTUI().run()


if __name__ == "__main__":
    main()
