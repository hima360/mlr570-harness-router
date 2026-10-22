"""Droid backend, credit pricing and tier scaling. Fake `droid exec`: no network, no credits."""
import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

from phase2 import droid
from phase2.config import load_config
from phase2.ledger import Ledger
from phase2.planner import Step
from phase2.router import Router
from phase2.worker import SUBMIT, HarborEnvAdapter, run_worker
from tests.fakes import FakeEnvironment, FakeScorer

ROOT = Path(__file__).resolve().parents[1]


def _result_line(text, **usage):
    u = {"input_tokens": 100, "output_tokens": 7, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 0, "factory_credits": 50, **usage}
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text, "usage": u})


def test_droid_complete_uses_fake_run():
    seen = {}

    def fake_run(argv, cwd, capture_output, text, timeout):
        seen["argv"] = argv
        seen["prompt"] = Path(argv[argv.index("-f") + 1]).read_text()
        out = "progress noise\n" + _result_line("hi there", input_tokens=10, cache_read_input_tokens=30,
                                                cache_creation_input_tokens=5, factory_credits=465)
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    text, usage = droid.complete("droid/glm-5.3-flash", [{"role": "system", "content": "be brief"},
                                                         {"role": "user", "content": "say hi"}], run=fake_run)
    assert text == "hi there"
    assert seen["argv"][:6] == ["droid", "exec", "-m", "glm-5.3-flash", "-o", "json"]
    assert "### SYSTEM\nbe brief" in seen["prompt"] and seen["prompt"].endswith("### ASSISTANT\n")
    assert usage["prompt_tokens"] == 45 and usage["prompt_tokens_details"]["cached_tokens"] == 30
    assert usage["completion_tokens"] == 7 and usage["factory_credits"] == 465


def test_droid_complete_raises_on_error_result():
    def fake_run(argv, **kw):
        line = json.dumps({"type": "result", "is_error": True, "result": "Provider not available"})
        return subprocess.CompletedProcess(argv, 1, stdout=line, stderr="")

    with pytest.raises(RuntimeError, match="droid exec failed"):
        droid.complete("droid/kimi-k3", [{"role": "user", "content": "x"}], run=fake_run)


def test_ledger_bills_droid_in_credits():
    from harbor.models.agent.context import AgentContext

    usage = {"prompt_tokens": 7314, "completion_tokens": 5, "factory_credits": 465}
    led = Ledger({}, credits_usd=0.001)
    assert led.add_from_usage("droid/glm-5.3-flash", usage) == pytest.approx(0.465)
    led.add_from_usage("droid/glm-5.3-flash", usage)
    ctx = AgentContext()
    led.fill_context(ctx)
    assert ctx.cost_usd == pytest.approx(0.93) and ctx.n_input_tokens == 2 * 7314
    assert ctx.metadata["factory_credits"] == {"droid/glm-5.3-flash": 930}

    unpriced = Ledger({})  # no USD rate known: credits counted, cost stays 0
    assert unpriced.add_from_usage("droid/gpt-6-sol", usage) == 0.0
    assert unpriced.credits_report()["factory_credits_total"] == 465


def test_droid_config_tiers():
    cfg = load_config(ROOT / "configs/phase2-droid.json")
    assert all(m.startswith("droid/") and "claude" not in m for m in cfg.tiers)
    assert cfg.planner_model == cfg.tiers[-1] == "droid/gpt-6-sol"
    mult = cfg.factory_credit["multipliers"]
    assert [mult[m] for m in cfg.tiers] == sorted(mult[m] for m in cfg.tiers)
    assert cfg.factory_credit["source"].startswith("https://") and cfg.factory_credit["checked"]


@pytest.mark.parametrize("n_tiers,score,tier", [
    (3, 0.9, 0), (3, 1.0, 1), (3, 2.0, 1),        # one routable tier above the cheapest
    (4, 0.4, 0), (4, 0.5, 1), (4, 1.5, 2),         # 0..2 maps 1:1
    (6, 1.0, 2), (6, 1.9, 4), (6, -1.0, 0),        # 0..4: score stretched, clipped
])
def test_router_scales_score_onto_tiers(n_tiers, score, tier):
    tiers = [f"droid/m{i}" for i in range(n_tiers)]
    cfg = load_config(tiers=tiers, planner_model=tiers[-1], policy="laya")
    assert Router(cfg, laya=FakeScorer((score, 1.0)), arch=None).route("s", "x").tier == tier


def test_worker_with_droid_text_model():
    env = FakeEnvironment(lambda cmd: (f"{SUBMIT}\nwrote hello.txt\n", 0) if SUBMIT in cmd else ("ok", 0))
    replies = iter([
        "THOUGHT: write it\n\n```mswea_bash_command\necho hello > /app/hello.txt\n```",
        f"THOUGHT: done\n\n```mswea_bash_command\nprintf '{SUBMIT}\\nwrote hello.txt\\n'\n```",
    ])
    prompts = []

    def fake_complete(model, messages):
        prompts.append(messages)
        return next(replies), {"prompt_tokens": 7000, "completion_tokens": 20,
                               "prompt_tokens_details": {"cached_tokens": 0}, "factory_credits": 420}

    led = Ledger({}, credits_usd=None)
    model_id = "droid/glm-5.3-flash"
    model = droid.make_text_model(model_id, lambda u: led.add_from_usage(model_id, u), complete_fn=fake_complete)

    async def main():
        adapter = HarborEnvAdapter(env, asyncio.get_running_loop(), 30)
        return await asyncio.to_thread(run_worker, load_config(), led, model_id, adapter, task_overall="T",
                                       step=Step(id="s1", goal="make hello.txt", writes=["/app/hello.txt"]),
                                       blackboard_text="-", model=model)

    res = asyncio.run(main())
    assert res.exit_status == "Submitted" and res.summary == "wrote hello.txt" and res.n_calls == 2
    assert env.commands[0] == "echo hello > /app/hello.txt"
    assert "mswea_bash_command" in prompts[0][-1]["content"]
    assert led.by_model[model_id].factory_credits == 840 and led.totals().calls == 2
