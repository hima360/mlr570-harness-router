import asyncio
import json
import os
import time

import pytest

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

from phase2.config import load_config
from phase2.ledger import Ledger
from phase2.orchestrator import Orchestrator
from phase2.planner import PlanError, make_plan, parse_plan
from phase2.router import Router
from phase2.scheduler import run_dag, writes_overlap
from phase2.trace import Trace
from phase2.worker import SUBMIT, HarborEnvAdapter, WorkerResult, run_worker
from tests.fakes import FakeEnvironment, FakeScorer


def plan_json(*steps):
    return json.dumps({"steps": [dict(id=i, goal=f"do {i}", depends_on=d, writes=w, check=c)
                                 for i, d, w, c in steps]})


# ---------- planner ----------

def test_parse_plan_accepts_valid_dag():
    p = parse_plan(plan_json(("s1", [], ["a.py"], ""), ("s2", ["s1"], ["b.py"], "true")), 8)
    assert [s.id for s in p.steps] == ["s1", "s2"]


@pytest.mark.parametrize("text,msg", [
    (plan_json(("s1", ["s2"], [], ""), ("s2", ["s1"], [], "")), "cycle"),
    (plan_json(("s1", ["zz"], [], "")), "unknown"),
    (plan_json(("s1", [], [], ""), ("s1", [], [], "")), "duplicate"),
    ("no json here", "no JSON"),
])
def test_parse_plan_rejects_bad_plans(text, msg):
    with pytest.raises(PlanError, match=msg):
        parse_plan(text, 8)


def test_parse_plan_enforces_step_limit():
    with pytest.raises(PlanError, match="steps"):
        parse_plan(plan_json(*[(f"s{i}", [], [], "") for i in range(3)]), 2)


def test_make_plan_repairs_then_falls_back():
    replies = iter(["garbage", plan_json(("s1", [], [], ""))])
    plan, info = make_plan("task", "ls", lambda m: next(replies), 8)
    assert info["attempts"] == 2 and not info["fallback"] and plan.steps[0].id == "s1"
    plan, info = make_plan("the task", "ls", lambda m: "garbage", 8)
    assert info["fallback"] and plan.steps[0].goal == "the task" and plan.steps[0].writes == ["**"]


# ---------- scheduler ----------

@pytest.mark.parametrize("a,b,expected", [
    (["src/a.py"], ["src/b.py"], False),
    (["src/a.py"], ["src/a.py"], True),
    (["src/*"], ["src/a.py"], True),
    (["lib/*.py"], ["lib2/x.py"], False),
    (["**"], ["anything"], True),
    ([], ["src/a.py"], False),
    (["./src/a.py"], ["src/a.py"], True),
])
def test_writes_overlap(a, b, expected):
    assert writes_overlap(a, b) is expected


def _steps(*specs):
    from phase2.planner import Step
    return [Step(id=i, goal=i, depends_on=d, writes=w) for i, d, w in specs]


def test_scheduler_respects_deps_and_overlap():
    order, active, peak = [], [0], [0]

    async def run_step(step):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        order.append(("start", step.id))
        await asyncio.sleep(0.02)
        active[0] -= 1
        order.append(("end", step.id))
        return step.id

    # a and b disjoint -> parallel; c writes same file as a -> waits; d depends on b
    steps = _steps(("a", [], ["x.py"]), ("b", [], ["y.py"]), ("c", [], ["x.py"]), ("d", ["b"], ["z.py"]))
    res = asyncio.run(run_dag(steps, run_step, max_parallel=3))
    assert set(res) == {"a", "b", "c", "d"}
    assert peak[0] >= 2
    assert order.index(("end", "a")) < order.index(("start", "c"))
    assert order.index(("end", "b")) < order.index(("start", "d"))


def test_scheduler_serial_when_max_parallel_1():
    peak, active = [0], [0]

    async def run_step(step):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.01)
        active[0] -= 1

    asyncio.run(run_dag(_steps(("a", [], ["1"]), ("b", [], ["2"])), run_step, max_parallel=1))
    assert peak[0] == 1


# ---------- router ----------

def test_router_policies():
    cfg = load_config(confidence_threshold=0.5)
    laya_sure, arch = FakeScorer((1.6, 0.9)), FakeScorer(0.2)
    r = Router(cfg, laya=laya_sure, arch=arch)
    d = r.route("s1", "x")
    assert (d.tier, d.source, arch.calls) == (2, "laya", 0)

    r = Router(cfg, laya=FakeScorer((1.6, 0.1)), arch=arch)
    d = r.route("s1", "x")
    assert (d.tier, d.source, arch.calls) == (0, "arch", 1)

    for policy, tier in (("fixed:3", 3), ("fixed:0", 0)):
        cfg.policy = policy
        assert Router(cfg, laya=laya_sure, arch=arch).route("s1", "x").tier == tier

    cfg.policy = "random"
    r = Router(cfg, laya=laya_sure, arch=arch)
    t1, t2 = r.route("s1", "x").tier, r.route("s1", "x").tier
    assert t1 == t2 and 0 <= t1 <= cfg.max_route_tier


def test_router_never_routes_to_escalation_tier():
    cfg = load_config(policy="laya")
    assert Router(cfg, laya=FakeScorer((9.0, 1.0)), arch=None).route("s", "x").tier == cfg.max_route_tier


def test_cascade_survives_missing_arch():
    def no_arch(text):
        raise ImportError("torch missing")

    cfg = load_config(confidence_threshold=0.9)
    d = Router(cfg, laya=FakeScorer((1.0, 0.1)), arch=no_arch).route("s", "x")
    assert d.source == "laya (arch unavailable)" and d.tier == 1


# ---------- ledger ----------

def test_ledger_cost_and_context():
    from harbor.models.agent.context import AgentContext

    led = Ledger(load_config().prices_per_million)
    c = led.add("anthropic/claude-sonnet-5-5", input_tokens=1_000_000, cached_tokens=500_000, output_tokens=100_000)
    assert c == pytest.approx(0.5 * 2 + 0.5 * 0.10 + 0.1 * 10)
    led.add_from_usage("zai/glm-5.3-flash", {"prompt_tokens": 2_000_000, "completion_tokens": 0,
                                             "prompt_tokens_details": {"cached_tokens": 0}})
    ctx = AgentContext()
    led.fill_context(ctx)
    assert ctx.n_input_tokens == 3_000_000 and ctx.n_cache_tokens == 500_000
    assert ctx.cost_usd == pytest.approx(2.05 + 0.30)
    assert set(ctx.model_usage) == {"anthropic/claude-sonnet-5-5", "zai/glm-5.3-flash"}


def test_config_rejects_unknown_keys_and_unpriced_models():
    with pytest.raises(ValueError, match="unknown"):
        load_config(bogus=1)
    with pytest.raises(ValueError, match="price"):
        load_config(tiers=["nope/model"])


# ---------- worker (real mini-swe-agent loop, scripted model, fake container) ----------

def _scripted_model(commands):
    from minisweagent.models.test_models import DeterministicModel, make_output
    return DeterministicModel(outputs=[make_output("thinking", [{"command": c}]) for c in commands])


def test_worker_runs_mini_swe_agent_until_submit():
    from phase2.planner import Step

    env = FakeEnvironment(lambda cmd: (f"{SUBMIT}\nedited a.py\n", 0) if SUBMIT in cmd else ("ok", 0))

    async def main():
        adapter = HarborEnvAdapter(env, asyncio.get_running_loop(), 30)
        model = _scripted_model(["ls", f"printf '{SUBMIT}\\nedited a.py\\n'"])
        return await asyncio.to_thread(
            run_worker, load_config(), None, "zai/glm-5.3-flash", adapter, task_overall="T",
            step=Step(id="s1", goal="g", writes=["a.py"]), blackboard_text="-", model=model)

    res = asyncio.run(main())
    assert res.exit_status == "Submitted" and res.summary == "edited a.py" and res.n_calls == 2
    assert env.commands[0] == "ls"


# ---------- orchestrator: escalation + accounting end to end ----------

def test_orchestrator_escalates_once_and_traces(tmp_path):
    cfg = load_config(policy="fixed:0")
    led, trace = Ledger(cfg.prices_per_million), Trace(tmp_path / "trace.jsonl")
    seen_models = []

    def work(step, model_id, bb_text, out_path):
        seen_models.append(model_id)
        led.add(model_id, 1000, 0, 100)
        return WorkerResult("Submitted", f"{step.id} done by {model_id}", 3)

    # s1's check passes only after escalation (second call); s2 passes first time.
    calls = {"s1": 0}

    def handler(cmd):
        if cmd == "check-s1":
            calls["s1"] += 1
            return ("", 0 if calls["s1"] >= 2 else 1)
        return ("", 0)

    def ask(messages):
        led.add(cfg.planner_model, 500, 0, 200)
        return plan_json(("s1", [], ["a.py"], "check-s1"), ("s2", ["s1"], ["b.py"], "true"))

    orch = Orchestrator(cfg, led, trace, Router(cfg, laya=None, arch=None), ask, work, tmp_path)
    summary = asyncio.run(orch.run("task", FakeEnvironment(handler)))
    assert summary["steps"]["s1"]["escalations"] == 1 and summary["steps"]["s1"]["passed"]
    assert summary["steps"]["s2"]["escalations"] == 0
    assert seen_models == [cfg.tiers[0], cfg.tiers[1], cfg.tiers[0]]
    events = [json.loads(l)["event"] for l in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert events.count("escalate") == 1 and events[0] == "plan" and events[-1] == "done"
    assert json.loads((tmp_path / "blackboard.json").read_text())["entries"]["s1"]["status"] == "done"
    assert led.totals().calls == 4


def test_orchestrator_marks_failure_after_one_retry(tmp_path):
    cfg = load_config(policy="fixed:3")  # already top tier: no escalation possible
    led = Ledger(cfg.prices_per_million)
    orch = Orchestrator(cfg, led, Trace(None), Router(cfg, laya=None, arch=None),
                        lambda m: plan_json(("s1", [], [], "false")),
                        lambda *a: WorkerResult("LimitsExceeded", "", 40), None)
    summary = asyncio.run(orch.run("task", FakeEnvironment(lambda c: ("", 1))))
    assert summary["n_failed_steps"] == 1 and summary["n_escalations"] == 0


def test_agent_class_loads_and_reads_overrides(tmp_path):
    from phase2.agent import RoutedHarness

    a = RoutedHarness(logs_dir=tmp_path, model_name=None, policy="fixed:3")
    assert a.name() == "routed-harness" and a.cfg.policy == "fixed:3"
    capped = RoutedHarness(logs_dir=tmp_path, model_name=None, max_plan_steps="5", worker_step_limit="25",
                           confidence_threshold="0.3")
    assert (capped.cfg.max_plan_steps, capped.cfg.worker_step_limit) == (5, 25)
    assert capped.cfg.confidence_threshold == 0.3


# ---------- replay, head training, trace view ----------

def _write_results(tmp_path):
    rows = []
    #            task   GLM            Sol            Sonnet
    table = {"t1": [(1, .1), (1, 1.), (1, 3.)],   # easy: GLM solves
             "t2": [(0, .2), (1, 1.), (1, 3.)],   # medium: Sol
             "t3": [(0, .2), (0, 1.), (0, 3.)]}   # nobody -> frontier label
    tiers = load_config().tiers[:-1]
    for task, cells in table.items():
        for m, (ok, c) in zip(tiers, cells):
            rows.append({"harness": "codex", "run": 1, "task": task, "model": m, "passed": bool(ok), "cost_usd": c})
    rows.append({"harness": "codex", "run": 2, "task": "t1", "model": tiers[0], "passed": False, "cost_usd": 9})
    (tmp_path / "x.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    return tiers


def test_replay_labels_and_policies(tmp_path, capsys):
    from phase2 import replay

    tiers = _write_results(tmp_path)
    grid = replay.load_grid(tmp_path, "codex")
    assert replay.labels(grid, tiers) == {"t1": 0, "t2": 1, "t3": 3}
    rep = replay.main(["--results", str(tmp_path), "--harness", "codex"])
    assert rep[f"always:{tiers[0]}"]["pass_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert rep["oracle"]["pass_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert rep["oracle"]["cost_usd"] == pytest.approx(0.1 + 1.0 + 3.0)
    assert rep[f"always:{tiers[-1]}"]["quality_retained"] == 1.0


def test_head_learns_separable_labels():
    import numpy as np

    from phase2.train_head import fit_softmax, kfold_eval, predict_proba

    rng = np.random.default_rng(0)
    y = np.repeat([0, 1, 2], 12)
    X = np.c_[y + rng.normal(0, .1, len(y)), rng.normal(0, 1, len(y))]
    assert (predict_proba(fit_softmax(X, y, 3), X).argmax(1) == y).mean() > 0.95
    assert kfold_eval(X, y, 3)["accuracy"] > 0.9


def test_show_trace_renders(tmp_path):
    from phase2.show_trace import render

    cfg = load_config(policy="fixed:0")
    trace = Trace(tmp_path / "trace.jsonl")
    orch = Orchestrator(cfg, Ledger(cfg.prices_per_million), trace, Router(cfg, laya=None, arch=None),
                        lambda m: plan_json(("s1", [], ["a.py"], "")),
                        lambda *a: WorkerResult("Submitted", "ok", 2), None)
    asyncio.run(orch.run("task", FakeEnvironment()))
    text = render(tmp_path / "trace.jsonl")
    assert "PLAN (1 steps" in text and "ROUTE s1 -> tier 0 via fixed" in text and "PASS s1" in text


def test_show_trace_live_view(tmp_path):
    from phase2.show_trace import find_agent_dir, render_live

    agent = tmp_path / "job" / "task__abc" / "agent"
    agent.mkdir(parents=True)
    trace = Trace(agent / "trace.jsonl")
    trace.log("route", step="s1", tier=0, source="laya", score=0.4, confidence=0.3, ms=20)
    (agent / "worker_s1_a1.json").write_text(json.dumps({"messages": [
        {"role": "system", "content": "sys"}, {"role": "user", "content": "task"},
        {"role": "assistant", "content": "THOUGHT: list files"}, {"role": "user", "content": "a.txt b.txt"}]}))
    assert find_agent_dir(tmp_path / "job") == agent
    view = render_live(agent)
    assert "ROUTE s1 -> tier 0" in view and "s1_a1: 1 model turns" in view and "shell < a.txt b.txt" in view
    (agent.parent / "result.json").write_text(json.dumps({"verifier_result": {"rewards": {"reward": 1.0}}}))
    assert "RESULT reward=1.0" in render_live(agent)
