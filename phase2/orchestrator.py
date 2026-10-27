"""Orchestrator: plan -> route -> work -> verify -> escalate, with every decision traced.

All model-facing parts are injected so the whole flow runs under tests with fakes:
  ask_planner(messages) -> str
  work(step, model_id, blackboard_text, output_path) -> WorkerResult   (blocking; run in a thread)
  router.route(step_id, text) -> RouteDecision
  ask_fixer(messages) -> str   (optional error handler; without it a failed step escalates once)
"""
import asyncio
import json
import re
from pathlib import Path

from .blackboard import Blackboard, facts_text
from .planner import load_pinned_plan, make_plan, merge_chain, plan_shape, single_step_plan, topo_order
from .scheduler import run_dag
from .cli_models import backend_of
from .verifier import Verdict, read_outputs, steps_dir, verify


REPAIR_STEP_LIMIT = 4  # model calls for the missing-outputs repair turn: write the file, submit
# Failures of the provider, not of the model's ability: code handles them (no handler call) and they
# are not counted as routing errors (Claude Code keeps provider fallback apart from escalation).
INFRA_EXITS = ("ProviderUnavailable", "ModelTimedOut", "StepIdle")
MAX_ATTEMPTS = 3  # per step: first try, one escalation, one more when the error handler asks for it

FIXER_ACTIONS = ("rerun_dependency", "retry", "escalate", "stop")
FIXER_PROMPT = """You are the error handler for a team of coding agents. A step failed. You do NOT do the work and
you cannot run commands: read the incident and choose ONE recovery action for the harness to carry out.

Reply with only one JSON object:
- {"action": "rerun_dependency", "step": "<earlier step id>", "note": "<what looks wrong in its result and how
  to verify it>"}: the failure comes from a wrong or missing result of an earlier step (a blocker note naming
  it, or a value that contradicts the evidence). That step is re-run on a stronger model, then this step retries.
- {"action": "retry", "note": "<one concrete hint>"}: the same model was close; a specific hint should fix it.
- {"action": "escalate", "note": "<what the stronger model should know>"}: the step is too hard for this model.
- {"action": "stop", "note": "<why>"}: nothing the team can do will fix it."""


def parse_fix(reply: str, allowed_steps, allowed_actions=FIXER_ACTIONS) -> dict:
    """The handler's decision, held to the menu: anything else becomes a plain escalation."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    try:
        a = json.loads(m.group(0)) if m else {}
    except ValueError:
        a = {}
    note = str((a or {}).get("note", ""))[:1000] if isinstance(a, dict) else ""
    if not isinstance(a, dict) or a.get("action") not in allowed_actions:
        return {"action": "escalate", "note": note, "invalid": True}
    if a["action"] == "rerun_dependency":
        if a.get("step") not in allowed_steps:
            return {"action": "escalate", "note": note, "invalid": True}
        return {"action": "rerun_dependency", "step": a["step"], "note": note}
    return {"action": a["action"], "note": note}


class Orchestrator:
    def __init__(self, cfg, ledger, trace, router, ask_planner, work, logs_dir: Path | None = None,
                 ask_fixer=None):
        self.cfg, self.ledger, self.trace, self.router = cfg, ledger, trace, router
        self.ask_planner, self.work, self.ask_fixer = ask_planner, work, ask_fixer
        self.interventions = 0
        self.env_block = ""
        self.steps, self.records = {}, {}
        self.logs_dir = logs_dir
        self.on_progress = lambda: None  # agent.py refreshes the Harbor context here

    async def run(self, instruction: str, environment) -> dict:
        env_summary = await self._env_summary(environment)
        # Droid: show the starting state as command output so workers do not re-run the same listing.
        self.env_block = f"\n\nAlready run at the start (do not repeat): pwd && ls -la\n{env_summary[:1500]}"
        if self.cfg.plan_from:  # same DAG as an earlier run: only the routing policy differs
            plan = load_pinned_plan(self.cfg.plan_from, self.cfg.max_plan_steps)
            plan_info = {"attempts": 0, "fallback": False, "errors": [], "pinned_from": str(self.cfg.plan_from)}
        elif self.cfg.max_plan_steps <= 1:  # nothing to plan: one agent does the task (keeps baselines pure)
            plan = single_step_plan(instruction)
            plan_info = {"attempts": 0, "fallback": False, "errors": [], "planner": "skipped"}
        else:
            plan, plan_info = await asyncio.to_thread(
                make_plan, instruction, env_summary, self.ask_planner, self.cfg.max_plan_steps)
        shape = plan_shape(plan)
        self.trace.log("plan", steps=[s.model_dump() for s in plan.steps], order=topo_order(plan),
                       planner_model=self.cfg.planner_model, **shape, **plan_info)
        for s in plan.steps:  # every planned part's folder, also when the parts are merged below
            try:
                await environment.exec(f"mkdir -p {steps_dir(s.id)}", timeout_sec=30)
            except Exception:
                pass
        if (self.cfg.merge_chains and len(plan.steps) > 1 and shape["parallelism"] <= 1.0
                and not any(s.continues for s in plan.steps)):  # continued chains keep their memory: no merge
            parts = [s.id for s in plan.steps]
            plan = merge_chain(plan, self.cfg.worker_step_limit, self.cfg.step_wall_time_sec)
            self.trace.log("merged", parts=parts, into=plan.steps[0].id,
                           worker_step_limit=plan.steps[0].worker_step_limit, check=plan.steps[0].check)
        self.steps = {s.id: s for s in plan.steps}
        bb = Blackboard(task=instruction)
        for s in plan.steps:
            bb.add(s.id, s.goal, s.writes, [o.name for o in s.outputs], s.depends_on)

        async def run_step(step):
            return await self._run_step(step, instruction, environment, bb)

        results = await run_dag(plan.steps, run_step, self.cfg.max_parallel)
        if self.logs_dir:
            bb.save(self.logs_dir / "blackboard.json")
        summary = {
            "plan": [s.model_dump() for s in plan.steps],
            "plan_info": plan_info,
            "steps": results,
            "n_escalations": sum(r["escalations"] for r in results.values()),
            "n_failed_steps": sum(1 for r in results.values() if not r["passed"]),
            "n_interventions": self.interventions,
            "policy": self.cfg.policy,
        }
        self.trace.log("done", **{k: summary[k] for k in ("n_escalations", "n_failed_steps", "n_interventions",
                                                          "policy")})
        return summary

    async def _env_summary(self, environment) -> str:
        try:
            r = await environment.exec("pwd && ls -la | head -50", timeout_sec=30)
            return (r.stdout or "")[:4000]
        except Exception as e:
            return f"(listing failed: {e})"

    async def _run_step(self, step, instruction, environment, bb) -> dict:
        decision = self.router.route(step.id, step.goal)
        self.trace.log("route", step=step.id, model=self.cfg.tiers[decision.tier], **decision.to_dict())
        record = {"route": decision.to_dict(), "attempts": [], "escalations": 0, "passed": False,
                  "tier": decision.tier}
        self.records[step.id] = record
        if why := self.over_budget():
            self.trace.log("stop", step=step.id, reason="budget", detail=why)
            bb.update(step.id, status="failed")
            return record
        try:
            await environment.exec(f"mkdir -p {steps_dir(step.id)}", timeout_sec=30)
        except Exception:
            pass  # the worker can still create it; read_outputs copes with a missing file
        handoff = ""
        while True:
            result, verdict, out_path = await self._attempt(step, record, handoff, environment, bb)
            if verdict.passed:
                record["passed"] = True
                break
            if len(record["attempts"]) >= MAX_ATTEMPTS:
                break
            if why := self.over_budget():
                self.trace.log("stop", step=step.id, reason="budget", detail=why)
                break
            infra = result.exit_status in INFRA_EXITS
            failed_deps = [d for d in step.depends_on if bb.entries[d].status == "failed"]
            if failed_deps and not infra:
                # A stronger model cannot supply a missing upstream result either (1026: s4 escalated to
                # Sonnet without s3's key and failed again). The best-effort first attempt is enough.
                self.trace.log("stop", step=step.id, reason="dependency_failed", failed_deps=failed_deps)
                break
            action = ({"action": "escalate"} if infra else
                      await self._triage(step, record, result, verdict, out_path, instruction, bb))
            kind, note = action["action"], action.get("note", "")
            model_id = self.cfg.tiers[record["tier"]]
            notes = self._handoff(out_path, model_id, result.exit_status, verdict.output)
            hint = f"\nThe error handler's note: {note}" if note else ""
            if kind == "stop":
                break
            if kind == "rerun_dependency":
                if await self._rerun(self.steps[action["step"]], note, environment, bb):
                    handoff = (f"\n\nEarlier step {action['step']} was re-run because: {note}\n"
                               f"Its corrected results are in the team status above.{notes}")
                    continue
                kind = "escalate"  # the earlier step could not be fixed: fall back
            if kind == "retry":
                handoff = notes + hint
                continue
            if record["escalations"] >= self.cfg.max_escalations or record["tier"] >= len(self.cfg.tiers) - 1:
                break
            if record["tier"] + 1 == len(self.cfg.tiers) - 1 and (why := self.over_budget(self.cfg.top_tier_budget_frac)):
                self.trace.log("stop", step=step.id, reason="top_tier_budget", detail=why)
                break
            record["escalations"] += 1
            self.trace.log("escalate", step=step.id, from_tier=record["tier"], to_tier=record["tier"] + 1,
                           router_error=not infra, reason=result.exit_status)
            handoff = notes + hint
            if handoff:
                self.trace.log("handoff", step=step.id, from_model=model_id,
                               to_model=self.cfg.tiers[record["tier"] + 1], chars=len(handoff))
            record["tier"] += 1
        bb.update(step.id, status="done" if record["passed"] else "failed")
        return record

    async def _attempt(self, step, record, handoff, environment, bb):
        """One worker attempt at the record's current tier: work, collect outputs (with one repair turn
        for missing ones), run the check, log it."""
        tier, n = record["tier"], len(record["attempts"]) + 1
        model_id = self.cfg.tiers[tier]
        bb.update(step.id, status="running", tier=tier, model=model_id, attempts=n)
        out_path = (self.logs_dir / f"worker_{step.id}_a{n}.json") if self.logs_dir else None
        team = bb.summary_for(step.id, step.depends_on)
        parent = (self.records.get(step.continues) or {}).get("session") if step.continues and n == 1 else None
        if parent and parent.get("backend") and parent["backend"] == backend_of(model_id):
            step = step.model_copy(update={"fork_from": parent})
            self.trace.log("fork", step=step.id, parent=step.continues, model=model_id)
        step = step.model_copy(update={"team_feed": bb.feed_for(step.id, step.depends_on)})
        result = await asyncio.to_thread(self.work, step, model_id, team + self.env_block + handoff, out_path)
        outputs, missing = await read_outputs(environment, step)
        if missing and result.exit_status in ("Submitted", "LimitsExceeded"):
            result, outputs, missing = await self._repair_outputs(
                step, model_id, team, environment, result, missing, out_path)
        verdict = await verify(environment, step, result.exit_status, self.cfg.check_timeout)
        if missing:  # promised results that later steps rely on are part of passing
            verdict = Verdict(False, f"missing declared outputs: {', '.join(missing)}\n{verdict.output}")
        self.trace.log("attempt", step=step.id, attempt=n, tier=tier, model=model_id,
                       exit_status=result.exit_status, n_calls=result.n_calls, passed=verdict.passed,
                       check=step.check, check_output=verdict.output[-500:], summary=result.summary[:300],
                       outputs={k: v[:200] for k, v in outputs.items()}, missing_outputs=missing)
        record["attempts"].append({"tier": tier, "model": model_id, "exit_status": result.exit_status,
                                   "passed": verdict.passed, "n_calls": result.n_calls})
        if verdict.passed and result.session:
            record["session"] = result.session  # a later step that continues this one forks it
        bb.update(step.id, summary=result.summary, check_output=verdict.output[-500:], outputs=outputs)
        self.on_progress()
        return result, verdict, out_path

    def over_budget(self, frac: float = 1.0) -> str:
        """Why spending must stop ("" = within budget): the run's list-price USD or Factory credits have
        reached `frac` of their cap."""
        from .ledger import budget_exceeded

        return budget_exceeded(self.cfg, self.ledger, frac)

    def _ancestors(self, step_id: str) -> list[str]:
        seen, todo = [], list(self.steps[step_id].depends_on) if step_id in self.steps else []
        while todo:
            d = todo.pop()
            if d not in seen and d in self.steps:
                seen.append(d)
                todo += self.steps[d].depends_on
        return seen

    async def _triage(self, step, record, result, verdict, out_path, instruction, bb) -> dict:
        """One fresh error-handler call (no shell, no memory between calls), within the run's budget."""
        if not self.ask_fixer or self.interventions >= self.cfg.max_interventions:
            return {"action": "escalate"}
        self.interventions += 1
        ran = [d for d in self._ancestors(step.id) if d in self.records]
        # Only offer what the harness can still do (1026: the handler chose escalate after the step's one
        # escalation was used, and the call was wasted).
        allowed = [a for a in FIXER_ACTIONS if not (
            (a == "escalate" and (record["escalations"] >= self.cfg.max_escalations or
                                  record["tier"] >= len(self.cfg.tiers) - 1)) or
            (a == "rerun_dependency" and not ran))]
        prompt = self._incident(step, record, result, verdict, out_path, instruction, bb, ran) + \
            f"\n\nAVAILABLE ACTIONS NOW: {', '.join(allowed)}"
        try:
            reply = await asyncio.to_thread(self.ask_fixer, [{"role": "user", "content": prompt}])
            action = parse_fix(reply, ran, allowed)
        except Exception as e:
            action = {"action": "escalate", "note": "", "error": str(e)[:300]}
        self.trace.log("triage", step=step.id, n=self.interventions,
                       **{("target" if k == "step" else k): v for k, v in action.items()})
        return action

    def _incident(self, step, record, result, verdict, out_path, instruction, bb, ran) -> str:
        from .worker import attempt_digest

        try:
            messages = json.loads(out_path.read_text()).get("messages", []) if out_path else []
        except (OSError, ValueError):
            messages = []
        own = bb.entries[step.id]
        lines = [FIXER_PROMPT, "", "TASK:", instruction[:1500], "",
                 f"FAILED STEP {step.id} (attempt {len(record['attempts'])} on {own.model}, ended: {result.exit_status})",
                 f"goal: {step.goal}", f"check: {step.check or '(none)'}",
                 f"check output: {verdict.output.strip()[-800:] or '(empty)'}",
                 f"its report: {result.summary[:400] or '(none)'}",
                 f"its published results: {facts_text(step.id, own.outputs, 1500) or '(none)'}", "",
                 "EARLIER STEPS IT BUILDS ON:" if ran else "It builds on no earlier step."]
        for d in ran:
            e = bb.entries[d]
            lines.append(f"- {d} [{e.status}, {e.model}] {e.goal[:200]}\n  results: "
                         f"{facts_text(d, e.outputs, 800) or '(none)'}")
        digest = attempt_digest(messages, max_chars=2500)
        if digest:
            lines += ["", "WHAT THE FAILED ATTEMPT DID (newest last):", digest]
        return "\n".join(lines)

    async def _rerun(self, dep, note, environment, bb) -> bool:
        """Re-open a finished earlier step one tier up, told what a later step found wrong."""
        rec = self.records[dep.id]
        if rec["tier"] + 1 >= len(self.cfg.tiers) - 1 and (why := self.over_budget(self.cfg.top_tier_budget_frac)):
            self.trace.log("stop", step=dep.id, reason="top_tier_budget", detail=why)
            return False
        rec["tier"] = min(rec["tier"] + 1, len(self.cfg.tiers) - 1)
        self.trace.log("rerun", step=dep.id, tier=rec["tier"], model=self.cfg.tiers[rec["tier"]],
                       note=note[:500], router_error=True)
        handoff = (f"\n\nYour step was finished before, but a later step found a problem with its result: {note}\n"
                   f"Verify against the source data and correct it: append the corrected name=value lines to "
                   f"{steps_dir(dep.id)}/outputs.txt (later lines win).")
        _, verdict, _ = await self._attempt(dep, rec, handoff, environment, bb)
        rec["passed"] = verdict.passed
        bb.update(dep.id, status="done" if verdict.passed else "failed")
        return verdict.passed

    async def _repair_outputs(self, step, model_id, team, environment, result, missing, out_path):
        """The worker submitted without publishing what the plan promised: one short turn on the same
        model to write them, so a format slip is not counted as a routing error."""
        self.trace.log("outputs_repair", step=step.id, model=model_id, missing=missing)
        fix = step.model_copy(update={"worker_step_limit": REPAIR_STEP_LIMIT, "goal": (
            f"Your step is already done ({step.goal[:600]}). It has NOT yet published these results: "
            f"{', '.join(missing)}. Write them now as described below (find the values in your earlier work, "
            f"the files you wrote and {steps_dir(step.id)}/), then submit. Do not redo the step.")})
        path = out_path.with_name(out_path.stem + "_repair.json") if out_path else None
        fixed = await asyncio.to_thread(self.work, fix, model_id, team, path)
        outputs, still = await read_outputs(environment, step)
        self.trace.log("outputs_repaired", step=step.id, model=model_id, missing=still, n_calls=fixed.n_calls)
        merged = type(result)(result.exit_status, result.summary, result.n_calls + fixed.n_calls)
        return merged, outputs, still

    @staticmethod
    def _handoff(out_path: Path | None, model_id: str, exit_status: str, check_output: str) -> str:
        """The failed attempt's findings, so the escalated attempt continues instead of starting over."""
        import json

        from .worker import attempt_digest

        try:
            messages = json.loads(out_path.read_text()).get("messages", []) if out_path else []
        except (OSError, ValueError):
            messages = []
        digest = attempt_digest(messages)
        if not digest:
            return ""
        check = f"\nThe step's check said: {check_output.strip()[-600:]}" if check_output.strip() else ""
        return (f"\n\nA previous attempt at YOUR step by {model_id} did not pass (ended: {exit_status}).{check}\n"
                f"Build on what it found instead of starting over; verify anything you rely on.\n{digest}")
