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
from .planner import Step, load_pinned_plan, make_plan, merge_chain, plan_shape, single_step_plan, topo_order
from .scheduler import run_dag
from .cli_models import backend_of
from .router import RouteDecision
from .verifier import Verdict, read_outputs, steps_dir, verify


DISCOVER_TESTS = r"""cd "$(pwd)" 2>/dev/null
[ -f package.json ] && grep -q '"test"' package.json && echo "npm test   (package.json)"
for f in pytest.ini tox.ini conftest.py; do [ -f "$f" ] && echo "python -m pytest   ($f)" && break; done
[ -f pyproject.toml ] && grep -q 'pytest' pyproject.toml && echo "python -m pytest   (pyproject.toml)"
[ -f Makefile ] && grep -qE '^(test|check):' Makefile && echo "make test   (Makefile)"
[ -f CMakeLists.txt ] && echo "build, then ctest --output-on-failure   (CMakeLists.txt)"
[ -f Cargo.toml ] && echo "cargo test   (Cargo.toml)"
[ -f go.mod ] && echo "go test ./...   (go.mod)"
find . -maxdepth 3 \( -name node_modules -o -name .git -o -name .venv \) -prune -o -type d \( -name tests -o -name test \) -print 2>/dev/null | head -3 | sed 's/^/tests folder: /'
find . -maxdepth 3 \( -name node_modules -o -name .git -o -name .venv \) -prune -o -type f \( -name 'test_*.py' -o -name '*_test.*' -o -name '*.test.*' \) -print 2>/dev/null | head -5 | sed 's/^/test file: /'
true"""
ACC_DIR = "/tmp/steps/acc"
# A final step's check also runs the independent acceptance tests, once they exist ("ready").
ACC_RUN = f"{{ [ ! -f {ACC_DIR}/ready ] || bash {ACC_DIR}/test.sh; }}"
ACC_GOAL = f"""Write ACCEPTANCE TESTS for the task above; do NOT implement the task and do not edit any file outside
{ACC_DIR}/. Other agents are implementing it at the same time; your tests will judge their work.
1. Read the task text closely and list every requirement it states (behaviours, scenarios, failure and edge
   cases, performance limits, output formats and paths, permissions: the task's own tests may run as a
   non-root user in a fresh shell) in {ACC_DIR}/requirements.md.
2. Write {ACC_DIR}/test.sh: a bash script that checks each stated requirement against the finished system,
   prints PASS or FAIL with a reason for each, and exits 0 only if all pass. Use only tools already in the
   container. Test only what the task states; do not invent requirements. Use APP="${{APP:-{{workdir}}}}" for
   every path in the task's work folder, so the script can also be pointed at a copy.
   The harness runs your script on an untouched copy of the starting files ({ACC_DIR}/orig, with APP set to
   it): it MUST FAIL there, or it cannot tell finished work from unfinished work. Make it strict enough.
3. The implementation may not exist yet, so the tests may fail now: that is expected. Make sure the script
   runs (bash -n {ACC_DIR}/test.sh) and fails clearly rather than crashing.
Then submit."""
ACC_HINT = (f"\nAn independent agent (acc) is writing acceptance tests from the task text at {ACC_DIR}/test.sh. "
            f"Once {ACC_DIR}/ready exists, run `bash {ACC_DIR}/test.sh` and fix your work until it passes before you "
            f"submit; do not edit {ACC_DIR}/.")

SNAPSHOT_MB = 500
REPAIR_STEP_LIMIT = 4  # model calls for the missing-outputs repair turn: write the file, submit
# Failures of the provider, not of the model's ability: code handles them (no handler call) and they
# are not counted as routing errors (Claude Code keeps provider fallback apart from escalation).
INFRA_EXITS = ("ProviderUnavailable", "ModelTimedOut", "StepIdle")
MAX_ATTEMPTS = 3  # per step: first try, one escalation, one more when the error handler asks for it

LEAD_PROMPT = """You are the lead of a team of coding agents working on this task:
{task}

Finished steps and what they found:
{done}

Steps that have not started yet (JSON, one per line):
{todo}

Using what the finished steps found (or why they failed), revise the steps that have not started only where it
clearly helps: make a goal more precise (name the files, values or approach now known), fix a check that would
not work, or change a difficulty (0 easy, 1 medium, 2 hard: it picks the model). Do not add or remove steps.
Reply with only JSON: {{"updates": [{{"id": "...", "goal": "...", "check": "...", "difficulty": 0}}]}}; use an empty
list when the plan is still right."""

ADVISOR_NOTES = ("\n\nPLANNER'S NOTES (hypotheses from a quick read-only look, not instructions: verify each before "
                 "you rely on it, and drop any the evidence contradicts):\n")

FIXER_ACTIONS = ("rerun_dependency", "retry", "escalate", "revise_check", "stop")
FIXER_PROMPT = """You are the error handler for a team of coding agents. A step failed. You do NOT do the work and
you cannot run commands: read the incident and choose ONE recovery action for the harness to carry out.

Reply with only one JSON object:
- {"action": "rerun_dependency", "step": "<earlier step id>", "note": "<what looks wrong in its result and how
  to verify it>"}: the failure comes from a wrong or missing result of an earlier step (a blocker note naming
  it, or a value that contradicts the evidence). That step is re-run on a stronger model, then this step retries.
- {"action": "retry", "note": "<one concrete hint>"}: the same model was close; a specific hint should fix it.
- {"action": "escalate", "note": "<what the stronger model should know>"}: the step is too hard for this model.
- {"action": "revise_check", "check": "<corrected shell command, or "" to drop the check>", "note": "<why the check
  is wrong>"}: the work meets the task text but the check is wrong. The planner wrote it before seeing the data, so
  it can assume what the task or the data contradicts (exact counts or formats it guessed, no tolerance for dirty
  data), test something the task never asked for, or be impossible to pass. A worker made to satisfy a wrong check
  moves away from the real goal, so prefer this over retry when that is the case. The new check is run on the
  current work; no new attempt is made unless it fails too.
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
    if a["action"] == "revise_check":
        if not isinstance(a.get("check"), str):
            return {"action": "escalate", "note": note, "invalid": True}
        return {"action": "revise_check", "check": a["check"].strip(), "note": note}
    return {"action": a["action"], "note": note}


class Orchestrator:
    def __init__(self, cfg, ledger, trace, router, ask_planner, work, logs_dir: Path | None = None,
                 ask_fixer=None):
        self.cfg, self.ledger, self.trace, self.router = cfg, ledger, trace, router
        self.ask_planner, self.work, self.ask_fixer = ask_planner, work, ask_fixer
        self.interventions = 0
        self.reviews, self._review_lock = 0, asyncio.Lock()
        self.env_block, self.workdir, self.snapshot = "", "/app", ""
        self.steps, self.records, self.single_step = {}, {}, False
        self.logs_dir = logs_dir
        self.on_progress = lambda: None  # agent.py refreshes the Harbor context here

    async def run(self, instruction: str, environment) -> dict:
        env_summary = await self._env_summary(environment)
        self.workdir = (env_summary.splitlines() or ["/app"])[0].strip() or "/app"
        tests_found = await self._discover_tests(environment)
        env_summary += tests_found
        if tests_found:
            self.trace.log("tests_found", text=tests_found.strip()[:1000])
        # Droid: show the starting state as command output so workers do not re-run the same listing.
        self.env_block = (f"\n\nAlready run at the start (do not repeat): pwd && ls -la\n{env_summary[:2500]}"
                          if self.cfg.env_bootstrap else tests_found)
        if self.cfg.plan_from:  # same DAG as an earlier run: only the routing policy differs
            plan = load_pinned_plan(self.cfg.plan_from, self.cfg.max_plan_steps)
            plan_info = {"attempts": 0, "fallback": False, "errors": [], "pinned_from": str(self.cfg.plan_from)}
        elif self.cfg.max_plan_steps <= 1:  # nothing to plan: one agent does the task (keeps baselines pure)
            plan = single_step_plan(instruction)
            plan_info = {"attempts": 0, "fallback": False, "errors": [], "planner": "skipped"}
        else:
            loop = asyncio.get_running_loop()
            explore = (lambda cmd: asyncio.run_coroutine_threadsafe(self._explore(environment, cmd), loop).result()) \
                if self.cfg.planner_explore else None
            plan, plan_info = await asyncio.to_thread(
                make_plan, instruction, env_summary, self.ask_planner, self.cfg.max_plan_steps, explore,
                self.cfg.planner_explore, self.cfg.planner_role == "advisor")
            if self.cfg.planner_role == "advisor":
                plan, plan_info["advisor"] = self._as_advisor(plan, instruction)
        self.single_step = (self.cfg.single_step_tier >= 0 and len(plan.steps) == 1 and not self.cfg.plan_from
                            and plan_info.get("planner") != "skipped")
        if self.single_step:  # the single-agent baseline's budget, not one planned part's
            plan.steps[0].worker_step_limit = 2 * self.cfg.worker_step_limit
            plan.steps[0].wall_time_sec = 2 * self.cfg.step_wall_time_sec
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
        acc = None
        if self.cfg.acceptance_model:
            # 2026-10-27 analysis: in 15 of 20 failed runs the step's own check passed but the task's tests
            # failed. An agent that never sees the implementation writes tests from the task text; the final
            # steps must pass them (Droid: validators independent of, and from another family than, the builder).
            acc = Step(id="acc", goal=ACC_GOAL.replace("{workdir}", self.workdir), writes=[f"{ACC_DIR}/**"],
                       check=(f"test -s {ACC_DIR}/test.sh && bash -n {ACC_DIR}/test.sh && "
                              f"{{ [ ! -d {ACC_DIR}/orig ] || ! APP={ACC_DIR}/orig/{Path(self.workdir).name} "
                              f"timeout 300 bash {ACC_DIR}/test.sh >/dev/null 2>&1; }}"))
            acc.worker_step_limit = self.cfg.acceptance_step_limit
            for s in self._sinks(plan.steps):
                s.check = f"( {s.check} ) && {ACC_RUN}" if s.check.strip() else ACC_RUN
                s.goal += ACC_HINT
            self.trace.log("acceptance_planned", model=self.cfg.acceptance_model,
                           final_steps=[s.id for s in self._sinks(plan.steps)])
        self.steps = {s.id: s for s in plan.steps}
        bb = Blackboard(task=instruction)
        for s in plan.steps:
            bb.add(s.id, s.goal, s.writes, [o.name for o in s.outputs], s.depends_on)
        if acc:
            bb.add("acc", "write acceptance tests from the task text (does not implement)", acc.writes, ["test.sh"], [])

        async def run_step(step):
            return await self._run_step(step, instruction, environment, bb)

        self.snapshot = await self._snapshot(environment) if (acc or self.cfg.best_of_models
                                                                or self.cfg.senior_reviewer_model) else ""
        acc_task = asyncio.create_task(self._run_acceptance(acc, instruction, environment, bb)) if acc else None
        results = await run_dag(plan.steps, run_step, self.cfg.max_parallel)
        if acc_task and await acc_task:
            await self._recheck_with_acceptance(plan.steps, results, instruction, environment, bb)
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

    def _as_advisor(self, plan, instruction):
        """Advisor mode: without independent workstreams the whole task is one step (the planner's notes kept);
        final steps get no planner check; notes reach workers as hypotheses, not as the design."""
        collapsed = len(plan.steps) > 1 and plan_shape(plan)["parallelism"] <= 1.0
        if collapsed:
            notes = "\n".join(f"- {s.notes.strip()}" for s in plan.steps if s.notes.strip())
            plan = single_step_plan(instruction)
            plan.steps[0].notes = notes
        sinks = {s.id for s in self._sinks(plan.steps)}
        for s in plan.steps:
            if s.id in sinks:
                s.check = ""
            if s.notes.strip():
                s.goal += ADVISOR_NOTES + s.notes.strip()
        return plan, {"collapsed": collapsed, "final_checks_dropped": sorted(sinks)}

    async def _best_of(self, step, record, environment, bb) -> None:
        """Several candidates attempt a final step at once, each in its own copy of the work folder and on its
        own model (diversity is the point); the one that passes most of the acceptance tests (or its check)
        is copied back into the work folder. Valid for file-based work; a task whose state lives in running
        services should keep best_of_models empty."""
        import shlex

        wd, models = self.workdir, list(self.cfg.best_of_models)
        cands = []
        for k, model in enumerate(models):
            copy = f"/tmp/cand/{k}/{Path(wd).name}"
            await environment.exec(f"rm -rf /tmp/cand/{k} && mkdir -p /tmp/cand/{k} && cp -a {self.snapshot} {copy}",
                                   timeout_sec=300)
            goal = (f"You are candidate {k + 1} of {len(models)} for this step. Work ONLY in {copy}, your private copy "
                    f"of {wd}: every path under {wd} in the task means the same path under {copy}. Never touch {wd}. "
                    f"Run acceptance tests with APP={copy} bash {ACC_DIR}/test.sh once {ACC_DIR}/ready exists.\n\n"
                    + step.goal.replace(wd, copy))
            cand = step.model_copy(update={"id": f"{step.id}c{k}", "goal": goal, "check": step.check.replace(wd, copy),
                                           "writes": [w.replace(wd, copy) for w in step.writes]})
            cands.append((k, model, copy, cand))
            await environment.exec(f"mkdir -p {steps_dir(cand.id)}", timeout_sec=30)
        team = bb.summary_for(step.id, step.depends_on, roster=self.cfg.team_roster)
        out = (lambda k: (self.logs_dir / f"worker_{step.id}_cand{k}.json") if self.logs_dir else None)
        results = await asyncio.gather(*[asyncio.to_thread(self.work, c, m, team + self.env_block, out(k))
                                         for k, m, _, c in cands])
        scores = []
        for (k, model, copy, cand), res in zip(cands, results):
            r = await environment.exec(f"cd {ACC_DIR} 2>/dev/null; [ -f {ACC_DIR}/ready ] && APP={shlex.quote(copy)} "
                                       f"timeout 300 bash {ACC_DIR}/test.sh 2>&1 | grep -c PASS; true", timeout_sec=330)
            passes = int((r.stdout or "0").strip().splitlines()[-1] or 0) if (r.stdout or "").strip() else 0
            v = await verify(environment, cand, res.exit_status, self.cfg.check_timeout)
            scores.append((v.passed, passes, -res.n_calls, k, model, copy, cand, res))
            self.trace.log("candidate", step=step.id, k=k, model=model, check_passed=v.passed, acceptance_passes=passes,
                           n_calls=res.n_calls, exit_status=res.exit_status)
        best = max(scores, key=lambda s: s[:3])
        _, _, _, k, model, copy, cand, res = best
        await environment.exec(f"cp -a {copy}/. {wd}/ && cp -f {steps_dir(cand.id)}/outputs.txt {steps_dir(step.id)}/ "
                               f"2>/dev/null; true", timeout_sec=300)
        verdict = await verify(environment, step, res.exit_status, self.cfg.check_timeout)
        self.trace.log("best_of", step=step.id, winner=k, model=model, passed=verdict.passed,
                       check_output=verdict.output[-300:])
        record["attempts"].append({"tier": record["tier"], "model": model, "exit_status": res.exit_status,
                                   "passed": verdict.passed, "n_calls": sum(r.n_calls for r in results),
                                   "best_of": len(cands)})
        outputs, _ = await read_outputs(environment, step)
        bb.update(step.id, summary=res.summary, outputs=outputs, check_output=verdict.output[-500:])
        record["passed"] = verdict.passed

    async def _snapshot(self, environment) -> str:
        """An untouched copy of the work folder before any agent starts (for validating acceptance tests and
        for best-of-N candidates). Skipped when the folder is over SNAPSHOT_MB."""
        d, dest = self.workdir, f"{ACC_DIR}/orig"
        cmd = (f"mkdir -p {dest} && s=$(du -sm {d} 2>/dev/null | cut -f1) && [ \"$s\" -lt {SNAPSHOT_MB} ] && "
               f"cp -a {d} {dest}/ && echo ok")
        try:
            r = await environment.exec(cmd, timeout_sec=300)
            ok = "ok" in (r.stdout or "")
        except Exception:
            ok = False
        self.trace.log("snapshot", ok=ok, path=f"{dest}/{Path(d).name}" if ok else "")
        return f"{dest}/{Path(d).name}" if ok else ""

    @staticmethod
    def _sinks(steps):
        return [s for s in steps if not any(s.id in t.depends_on for t in steps)]

    async def _run_acceptance(self, acc, instruction, environment, bb) -> bool:
        """The acceptance-test writer runs beside the implementers; when its script exists and parses, it
        marks it ready, so final steps' checks start running it."""
        model = self.cfg.acceptance_model
        try:
            await environment.exec(f"mkdir -p {ACC_DIR}", timeout_sec=30)
        except Exception:
            pass
        bb.update("acc", status="running", model=model, attempts=1)
        out_path = (self.logs_dir / "worker_acc_a1.json") if self.logs_dir else None
        team = bb.summary_for("acc", (), roster=self.cfg.team_roster)
        result = await asyncio.to_thread(self.work, acc, model, team + self.env_block, out_path)
        verdict = await verify(environment, acc, result.exit_status, self.cfg.check_timeout)
        if verdict.passed:
            try:
                await environment.exec(f"touch {ACC_DIR}/ready", timeout_sec=30)
            except Exception:
                verdict = Verdict(False, "could not mark the acceptance tests ready")
        self.trace.log("acceptance", model=model, exit_status=result.exit_status, n_calls=result.n_calls,
                       ready=verdict.passed, check_output=verdict.output[-300:])
        bb.update("acc", status="done" if verdict.passed else "failed", summary=result.summary)
        return verdict.passed

    async def _recheck_with_acceptance(self, steps, results, instruction, environment, bb) -> None:
        """A final step that passed before the tests were ready is checked again; if the tests fail, it is
        re-opened once on the same model with their output (within budget)."""
        for s in self._sinks(steps):
            record = results.get(s.id)
            if not record or not record["passed"]:
                continue
            r = await verify(environment, Step(id=s.id, goal="-", check=f"bash {ACC_DIR}/test.sh"), "Submitted",
                             self.cfg.check_timeout)
            self.trace.log("acceptance_check", step=s.id, passed=r.passed, output=r.output[-500:])
            if r.passed or self.over_budget():
                continue
            handoff = (f"\n\nYour step passed its own check, but the independent acceptance tests written from the "
                       f"task text fail ({ACC_DIR}/test.sh). Their output:\n{r.output.strip()[-1500:]}\n"
                       f"Fix your work so they pass (if a test is clearly wrong about the task text, say so in your "
                       f"submit message), then submit.")
            self.trace.log("reopen", step=s.id, reason="acceptance_failed")
            _, verdict, _ = await self._attempt(s, record, handoff, environment, bb)
            record["passed"] = verdict.passed
            bb.update(s.id, status="done" if verdict.passed else "failed")

    async def _explore(self, environment, command: str) -> str:
        """One read-only command for the planner (refused unless every pipe segment only reads)."""
        from .planner import read_only

        if not read_only(command):
            out = "Refused: only read-only commands are allowed (ls, cat, head, grep, find, wc, sed -n, git log/diff/show)."
        else:
            try:
                r = await environment.exec(f"cd {self.workdir} 2>/dev/null; {command}", timeout_sec=30)
                out = ((r.stdout or "") + (r.stderr or ""))[:4000] or "(no output)"
            except Exception as e:
                out = f"(failed: {e})"
        self.trace.log("explore", command=command[:300], chars=len(out))
        return out

    async def _discover_tests(self, environment) -> str:
        """The task's own test commands, found once at the start: hidden tests usually look like them."""
        try:
            r = await environment.exec(DISCOVER_TESTS, timeout_sec=30)
            found = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()][:8]
        except Exception:
            found = []
        return ("\n\nTests that already exist in the task (run the relevant ones before you submit; the task's "
                "own grading tests probably look like these):\n" + "\n".join(f"- {f}" for f in found)) if found else ""

    async def _env_summary(self, environment) -> str:
        try:
            r = await environment.exec("pwd && ls -la | head -50", timeout_sec=30)
            return (r.stdout or "")[:4000]
        except Exception as e:
            return f"(listing failed: {e})"

    async def _run_step(self, step, instruction, environment, bb) -> dict:
        if self.single_step and step.id in self.steps:  # one planned step: the single-agent setup, no rating used
            tier = min(self.cfg.single_step_tier, len(self.cfg.tiers) - 1)
            decision = RouteDecision(tier, "single_step", router=f"one-step plan: tier {tier}, as the single agent")
        else:
            decision = self.router.route(step.id, step.goal, hint=step.difficulty)
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
        if (self.cfg.best_of_models and self.snapshot and step.id in {s.id for s in self._sinks(self.steps.values())}
                and not self.over_budget(self.cfg.top_tier_budget_frac)):
            await self._best_of(step, record, environment, bb)
            if record["passed"]:
                bb.update(step.id, status="done")
                self.trace.log("step_end", step=step.id, passed=True, attempts=len(record["attempts"]))
                return record
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
            if kind == "revise_check":
                record["check_revised"] = True
                old, step.check = step.check, action["check"]
                if ACC_RUN in old and ACC_RUN not in step.check:  # the acceptance tests stay part of a final check
                    step.check = f"( {step.check} ) && {ACC_RUN}" if step.check else ACC_RUN
                verdict = await verify(environment, step, result.exit_status, self.cfg.check_timeout)
                self.trace.log("check_revised", step=step.id, old=old, new=step.check, passed=verdict.passed,
                               check_output=verdict.output[-500:], note=note[:500])
                if verdict.passed:
                    record["passed"] = True
                    break
                kind, hint = "retry", hint + f"\nThe step's check was corrected to: {step.check}"
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
        self.trace.log("step_end", step=step.id, passed=record["passed"], attempts=len(record["attempts"]))
        await self._lead_review(step, record, instruction, bb)
        return record

    async def _lead_review(self, step, record, instruction, bb) -> None:
        """A lead check-in: after a step ends, the planner may revise the goals, checks and difficulty of steps
        that have not started, using what just happened (facts found, a failure). At most cfg.lead_reviews."""
        pending = [s for s in self.steps.values() if bb.entries[s.id].status == "pending"]
        if not pending or self.reviews >= self.cfg.lead_reviews or self.over_budget(self.cfg.top_tier_budget_frac):
            return
        async with self._review_lock:
            if self.reviews >= self.cfg.lead_reviews:
                return
            self.reviews += 1
            done = "\n".join(f"- {sid} [{e.status}] {e.goal[:200]}\n  results: {facts_text(sid, e.outputs, 600) or '-'}"
                             f"\n  report: {e.summary[:300] or '-'}" for sid, e in bb.entries.items()
                             if e.status in ("done", "failed"))
            todo = "\n".join(json.dumps({"id": s.id, "goal": s.goal[:1500], "check": s.check,
                                          "difficulty": s.difficulty}) for s in pending)
            prompt = LEAD_PROMPT.format(task=instruction[:2000], done=done, todo=todo)
            try:
                reply = await asyncio.to_thread(self.ask_planner, [{"role": "user", "content": prompt}])
                m = re.search(r"\{.*\}", reply or "", re.S)
                updates = (json.loads(m.group(0)).get("updates") or []) if m else []
            except Exception as e:
                self.trace.log("lead_review", after=step.id, error=str(e)[:300])
                return
            changed = []
            for u in updates:
                s = self.steps.get(u.get("id")) if isinstance(u, dict) else None
                if s is None or bb.entries[s.id].status != "pending":
                    continue  # only steps that have not started can change
                sink = s in self._sinks(list(self.steps.values()))
                for k in ("goal", "check"):
                    if k == "check" and sink and self.cfg.planner_role == "advisor":
                        continue  # advisor mode: final steps stay without a planner check
                    if isinstance(u.get(k), str) and u[k].strip():
                        setattr(s, k, u[k].strip())
                if u.get("difficulty") in (0, 1, 2):
                    s.difficulty = u["difficulty"]
                bb.entries[s.id].goal = s.goal
                changed.append(s.id)
            self.trace.log("lead_review", after=step.id, changed=changed, n=self.reviews)

    async def _attempt(self, step, record, handoff, environment, bb):
        """One worker attempt at the record's current tier: work, collect outputs (with one repair turn
        for missing ones), run the check, log it."""
        tier, n = record["tier"], len(record["attempts"]) + 1
        model_id = self.cfg.tiers[tier]
        bb.update(step.id, status="running", tier=tier, model=model_id, attempts=n)
        out_path = (self.logs_dir / f"worker_{step.id}_a{n}.json") if self.logs_dir else None
        team = bb.summary_for(step.id, step.depends_on, roster=self.cfg.team_roster)
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
        # The check can be revised once per step, when the worker submitted and only the check failed (not missing
        # outputs). Two such failures make the check itself the suspect: the handler is then called even past
        # max_interventions (2026-10-28 telecom: two easy parse steps failed their checks 3 times each, also on
        # Opus, after the run's handler calls were used up).
        revisable = (bool(step.check.strip()) and result.exit_status == "Submitted"
                     and not verdict.output.startswith("missing declared outputs") and not record.get("check_revised"))
        same_check_fails = sum(a["exit_status"] == "Submitted" and not a["passed"] for a in record["attempts"])
        suspect = revisable and same_check_fails >= 2
        if not self.ask_fixer or (self.interventions >= self.cfg.max_interventions and not suspect):
            return {"action": "escalate"}
        self.interventions += 1
        ran = [d for d in self._ancestors(step.id) if d in self.records]
        # Only offer what the harness can still do (1026: the handler chose escalate after the step's one
        # escalation was used, and the call was wasted).
        allowed = [a for a in FIXER_ACTIONS if not (
            (a == "escalate" and (record["escalations"] >= self.cfg.max_escalations or
                                  record["tier"] >= len(self.cfg.tiers) - 1)) or
            (a == "rerun_dependency" and not ran) or (a == "revise_check" and not revisable))]
        models = sorted({a["model"] for a in record["attempts"]})
        prompt = self._incident(step, record, result, verdict, out_path, instruction, bb, ran) + (
            f"\n\nNOTE: {same_check_fails} attempts ({', '.join(models)}) submitted this step as done and every one "
            f"failed the same check. Suspect the check before the workers." if suspect else "") + \
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
