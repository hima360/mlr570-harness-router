"""CLI model backends (Droid, Claude Code), credit pricing and tier scaling. Fake CLIs: no network."""
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
    assert seen["argv"][0].endswith("droid") and seen["argv"][1:6] == ["exec", "-m", "glm-5.3-flash", "-o", "json"]
    assert seen["argv"][seen["argv"].index("--only-tools") + 1] == "TodoWrite"
    assert "### SYSTEM\nbe brief" in seen["prompt"] and seen["prompt"].endswith("### ASSISTANT\n")
    assert usage["prompt_tokens"] == 45 and usage["prompt_tokens_details"]["cached_tokens"] == 30
    assert usage["completion_tokens"] == 7 and usage["factory_credits"] == 465


def test_droid_binary_choice(monkeypatch, tmp_path):
    monkeypatch.setenv("PHASE2_DROID_BIN", "/opt/droid")
    assert droid.droid_bin() == "/opt/droid"
    monkeypatch.delenv("PHASE2_DROID_BIN")
    monkeypatch.setattr(droid, "APP_DROID", str(tmp_path / "missing"))
    assert droid.droid_bin() == "droid"
    app = tmp_path / "droid"
    app.write_text("")
    monkeypatch.setattr(droid, "APP_DROID", str(app))
    assert droid.droid_bin() == str(app)


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


@pytest.mark.parametrize("model_id", ["droid/glm-5.3-flash", "claude/claude-haiku-5-5"])
def test_worker_with_cli_text_model(model_id, capfd):
    from phase2.cli_models import make_text_model

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

    led = Ledger(load_config(ROOT / "configs/phase2-claude-code.json").prices_per_million, credits_usd=None)
    model = make_text_model(model_id, lambda u, *_: led.add_from_usage(model_id, u), complete_fn=fake_complete)

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
    assert "Provider List" not in "".join(capfd.readouterr())  # no litellm pricing lookups for CLI ids


# ---------- Claude Code backend (fake `claude -p`, no subscription usage) ----------

def test_cli_backend_dispatch():
    from phase2 import claude_code
    from phase2.cli_models import complete_fn_for

    assert complete_fn_for("droid/glm-5.3-flash") is droid.complete
    assert complete_fn_for("claude/claude-haiku-5-5") is claude_code.complete
    assert complete_fn_for("anthropic/claude-opus-5-5") is None  # opencodex id


def test_claude_code_complete_uses_fake_run(monkeypatch):
    from phase2 import claude_code

    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    seen = {}

    def fake_run(argv, input, cwd, env, capture_output, text, timeout):
        seen.update(argv=argv, input=input, env=env)
        out = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done",
                          "total_cost_usd": 0.0123,
                          "usage": {"input_tokens": 20, "cache_creation_input_tokens": 100,
                                    "cache_read_input_tokens": 300, "output_tokens": 9}})
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    text, usage = claude_code.complete("claude/claude-haiku-5-5",
                                       [{"role": "system", "content": "be a shell agent"},
                                        {"role": "user", "content": "list files"}], run=fake_run)
    argv = seen["argv"]
    assert text == "done" and argv[:4] == ["claude", "-p", "--model", "claude-haiku-5-5"]
    assert argv[argv.index("--tools") + 1] == "" and "--safe-mode" in argv
    assert "be a shell agent" in argv[argv.index("--system-prompt") + 1]
    assert "### USER\nlist files" in seen["input"] and "be a shell agent" not in seen["input"]
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert usage["prompt_tokens"] == 420 and usage["prompt_tokens_details"]["cached_tokens"] == 300
    assert usage["completion_tokens"] == 9 and usage["claude_code_cost_usd"] == 0.0123


def test_claude_code_config_and_list_price_cost():
    from harbor.models.agent.context import AgentContext

    cfg = load_config(ROOT / "configs/phase2-claude-code.json")
    assert cfg.planner_model == cfg.tiers[-1] == "claude/claude-opus-5-5" and cfg.max_route_tier == 1
    led = Ledger(cfg.prices_per_million)
    c = led.add_from_usage("claude/claude-sonnet-5-5", {"prompt_tokens": 1_000_000, "completion_tokens": 100_000,
                                                        "prompt_tokens_details": {"cached_tokens": 500_000},
                                                        "claude_code_cost_usd": 3.5})
    assert c == pytest.approx(0.5 * 2 + 0.5 * 0.10 + 0.1 * 10)
    ctx = AgentContext()
    led.fill_context(ctx)
    assert ctx.metadata == {"claude_code_cost_usd": {"claude/claude-sonnet-5-5": 3.5}}


# ---------- effort pinning and retry ----------

@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    from phase2 import cli_models

    monkeypatch.setattr(cli_models, "RETRY_WAIT_SEC", 0)


def _ok_run(calls, fail_first=0, fail_stdout="Invalid model: x"):
    def run(argv, **kw):
        calls.append(argv)
        if len(calls) <= fail_first:
            return subprocess.CompletedProcess(argv, 1, stdout=fail_stdout, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout=_result_line("ok"), stderr="")
    return run


def test_effort_comes_from_config():
    from phase2.cli_models import complete_fn_for

    for path, model, flag in (("configs/phase2-droid.json", "droid/gpt-6-sol", "-r"),
                              ("configs/phase2-claude-code.json", "claude/claude-opus-5-5", "--effort")):
        cfg = load_config(ROOT / path)
        assert set(cfg.reasoning_effort) == set(cfg.tiers)
        calls = []
        complete_fn_for(model, cfg)(model, [{"role": "user", "content": "x"}], run=_ok_run(calls))
        assert calls[0][calls[0].index(flag) + 1] == cfg.reasoning_effort[model]
    calls = []
    droid.complete("droid/kimi-k3", [{"role": "user", "content": "x"}], run=_ok_run(calls))
    assert "-r" not in calls[0]  # no pin -> CLI default


def test_cli_retries_once_when_no_turn_ran():
    calls = []
    text, _ = droid.complete("droid/glm-5.3-flash", [{"role": "user", "content": "x"}],
                             run=_ok_run(calls, fail_first=1))
    assert text == "ok" and len(calls) == 2

    zero_turns = json.dumps({"type": "result", "is_error": True, "num_turns": 0, "result": "Exec failed"})
    calls = []
    with pytest.raises(RuntimeError):
        droid.complete("droid/glm-5.3-flash", [{"role": "user", "content": "x"}],
                       run=_ok_run(calls, fail_first=9, fail_stdout=zero_turns))
    assert len(calls) == 2


def test_cli_does_not_retry_after_a_model_turn():
    from phase2 import claude_code

    after_turn = json.dumps({"type": "result", "is_error": True, "num_turns": 1,
                             "result": "Failed to authenticate"})
    calls = []
    with pytest.raises(RuntimeError, match="authenticate"):
        claude_code.complete("claude/claude-haiku-5-5", [{"role": "user", "content": "x"}],
                             run=_ok_run(calls, fail_first=9, fail_stdout=after_turn))
    assert len(calls) == 1


def test_cli_timeout_from_config_and_retried_once():
    from phase2.cli_models import complete_fn_for

    cfg = load_config(ROOT / "configs/phase2-droid.json", cli_timeout_sec=42)
    seen = []

    def stalls_once(argv, timeout, **kw):
        seen.append(timeout)
        if len(seen) == 1:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, 0, stdout=_result_line("ok"), stderr="")

    text, _ = complete_fn_for("droid/glm-5.3-flash", cfg)("droid/glm-5.3-flash", [{"role": "user", "content": "x"}],
                                                       run=stalls_once)
    assert text == "ok" and seen == [42, 42]

    def always_stalls(argv, timeout, **kw):
        raise subprocess.TimeoutExpired(argv, timeout)

    with pytest.raises(RuntimeError, match="timed out after 42 s"):
        complete_fn_for("droid/glm-5.3-flash", cfg)("droid/glm-5.3-flash", [{"role": "user", "content": "x"}],
                                                   run=always_stalls)


def test_run_group_timeout_kills_helper_processes():
    import time

    from phase2.cli_models import run_group

    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_group(["sh", "-c", "sleep 30 & sleep 30"], timeout=0.5)  # background child holds the pipe
    assert time.monotonic() - t0 < 5
    assert run_group(["sh", "-c", "cat"], input="hi", timeout=5).stdout == "hi"


# ---------- stall prevention: step wall-clock budget and output clipping ----------

def test_worker_stops_at_step_wall_time():
    import time as _time

    from phase2.cli_models import make_text_model

    env = FakeEnvironment(lambda cmd: ("ok", 0))

    def slow_complete(model, messages):
        _time.sleep(0.6)
        return "THOUGHT: look\n\n```mswea_bash_command\nls\n```", {"prompt_tokens": 10, "completion_tokens": 2}

    led = Ledger({}, credits_usd=None)
    model_id = "droid/glm-5.3-flash"
    model = make_text_model(model_id, lambda u, *_: led.add_from_usage(model_id, u), complete_fn=slow_complete)
    cfg = load_config(step_wall_time_sec=1, worker_step_limit=50)

    async def main():
        adapter = HarborEnvAdapter(env, asyncio.get_running_loop(), 30)
        return await asyncio.to_thread(run_worker, cfg, led, model_id, adapter, task_overall="T",
                                       step=Step(id="s1", goal="g"), blackboard_text="-", model=model)

    t0 = _time.monotonic()
    res = asyncio.run(main())
    assert res.exit_status == "TimeExceeded" and res.n_calls == 2 and _time.monotonic() - t0 < 3


def test_long_command_output_is_clipped():
    from phase2.worker import clip_output

    env = FakeEnvironment(lambda cmd: ("A" * 6000 + "B" * 6000, 0))

    async def main():
        adapter = HarborEnvAdapter(env, asyncio.get_running_loop(), 30, max_output_chars=1000)
        return await asyncio.to_thread(adapter.execute, {"command": "cat big"})  # sync API, worker thread

    out = asyncio.run(main())["output"]
    assert out.startswith("A" * 500) and out.endswith("B" * 500) and "11000 characters elided" in out
    assert clip_output("short", 1000) == "short" and clip_output("x" * 50, 0) == "x" * 50


# ---------- who did what: every actor is recorded ----------

def test_actor_records_for_planner_router_and_worker(monkeypatch):
    from phase2 import llm
    from phase2.cli_models import make_text_model

    monkeypatch.setattr(droid, "complete", lambda model, messages, **kw: (
        "PLAN TEXT", {"prompt_tokens": 900, "completion_tokens": 40, "factory_credits": 700}))
    cfg = load_config(ROOT / "configs/phase2-droid.json")
    calls = []
    assert llm.chat(cfg, Ledger({}), "droid/gpt-6-sol", [{"role": "user", "content": "plan"}],
                    on_call=calls.append) == "PLAN TEXT"
    assert calls[0]["model"] == "droid/gpt-6-sol" and calls[0]["input_tokens"] == 900
    assert calls[0]["factory_credits"] == 700 and calls[0]["reply"] == "PLAN TEXT" and "ms" in calls[0]

    d = Router(load_config(policy="laya"), laya=FakeScorer((1.0, 0.9)), arch=None).route("s", "x")
    assert d.router == "FakeScorer"
    assert Router(load_config(policy="fixed:2"), laya=None, arch=None).route("s", "x").router.startswith("fixed policy")
    from phase2.router import LayaScorer
    assert Router(load_config(policy="laya"), laya=LayaScorer(), arch=None).laya.name.startswith("Laya")

    worker_calls = []
    led = Ledger({})
    from phase2.worker import _make_model
    m = _make_model(cfg, led, "droid/glm-5.3-flash", on_call=worker_calls.append)
    m._query([{"role": "user", "content": "go"}])
    assert worker_calls[0]["model"] == "droid/glm-5.3-flash" and led.totals().calls == 1


def test_idle_limit_stops_a_stuck_step_but_not_a_slow_one():
    import time as _time

    from phase2.cli_models import make_text_model

    env = FakeEnvironment(lambda cmd: (f"{SUBMIT}\ndone\n", 0) if SUBMIT in cmd else ("ok", 0))
    model_id = "droid/glm-5.3-flash"

    def run(complete, idle):
        led = Ledger({})
        model = make_text_model(model_id, lambda u, *_: led.add_from_usage(model_id, u), complete_fn=complete,
                                idle_sec=idle)
        cfg = load_config(step_idle_sec=idle, worker_step_limit=20)

        async def main():
            adapter = HarborEnvAdapter(env, asyncio.get_running_loop(), 30)
            return await asyncio.to_thread(run_worker, cfg, led, model_id, adapter, task_overall="T",
                                           step=Step(id="s1", goal="g"), blackboard_text="-", model=model)
        return asyncio.run(main())

    replies = iter(["```mswea_bash_command\nls\n```"] * 3 + [f"```mswea_bash_command\nprintf '{SUBMIT}\\ndone\\n'\n```"])

    def slow_but_working(model, messages):
        _time.sleep(0.4)
        return next(replies), {"prompt_tokens": 1, "completion_tokens": 1}

    assert run(slow_but_working, 0.6).exit_status == "Submitted"  # 4 calls x 0.4 s > 0.6 s total, never idle

    def stuck(model, messages):
        _time.sleep(0.7)
        raise RuntimeError("droid exec failed: Connection error.")

    t0 = _time.monotonic()
    res = run(stuck, 0.5)
    assert res.exit_status == "StepIdle" and "no model reply" in res.summary and _time.monotonic() - t0 < 10
