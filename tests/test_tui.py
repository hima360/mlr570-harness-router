"""TUI and job management: fake job dirs, fake launcher/stopper, headless Textual pilot. No Docker."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from phase2 import jobs as J
from phase2.tui import HarnessTUI, activity_lines, roles_text, step_rows

EVENTS = [
    {"t": 0.0, "event": "config", "tiers": ["droid/glm-5.3-flash", "droid/deepseek-v4.1-flash", "droid/gpt-6-sol"],
     "planner_model": "droid/gpt-6-sol", "policy": "laya", "confidence_threshold": 0.35},
    {"t": 9.0, "event": "llm_call", "role": "planner", "model": "droid/gpt-6-sol", "ms": 8000,
     "input_tokens": 8600, "output_tokens": 60, "factory_credits": 7000, "cost_usd": 0, "reply": "{...}"},
    {"t": 9.1, "event": "plan", "planner_model": "droid/gpt-6-sol", "fallback": False,
     "steps": [{"id": "s1", "goal": "make hello.txt", "depends_on": [], "writes": [], "check": "test -f x"}]},
    {"t": 9.5, "event": "route", "step": "s1", "model": "droid/glm-5.3-flash", "tier": 0, "source": "laya",
     "router": "Laya (laya_mlx, local MLX)", "score": 0.4, "confidence": 0.3, "ms": 20},
    {"t": 12.0, "event": "llm_call", "role": "worker", "step": "s1", "model": "droid/glm-5.3-flash", "ms": 2500,
     "input_tokens": 3300, "output_tokens": 20, "factory_credits": 200,
     "reply": "THOUGHT: write it ```mswea_bash_command echo hello > /app/hello.txt ```"},
    {"t": 15.0, "event": "attempt", "step": "s1", "attempt": 1, "tier": 0, "model": "droid/glm-5.3-flash",
     "exit_status": "Submitted", "n_calls": 1, "passed": True, "check": "test -f x"},
    {"t": 15.1, "event": "done", "n_escalations": 0, "n_failed_steps": 0, "policy": "laya"},
]


def make_job(root: Path, name="demo", reward=1.0, exception=None) -> Path:
    job = root / "jobs" / name
    agent = job / "hello-file__abc123" / "agent"
    agent.mkdir(parents=True)
    (job / "config.json").write_text(json.dumps({"agents": [{"kwargs": {"config": "configs/phase2-droid.json",
                                                                        "policy": "laya", "max_plan_steps": 5}}],
                                                 "tasks": [{"path": "tests/harbor_smoke/hello-file"}]}))
    (agent / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in EVENTS) + "\n")
    (agent / "worker_s1_a1.json").write_text(json.dumps({"messages": [
        {"role": "system", "content": "s"}, {"role": "user", "content": "task"},
        {"role": "assistant", "content": "THOUGHT: write it"}, {"role": "user", "content": "ok"}]}))
    result = {"task_name": "hello-file", "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
              "exception_info": exception}
    (job / "hello-file__abc123" / "result.json").write_text(json.dumps(result))
    return job


def _plain(rich_text) -> str:
    return rich_text.plain if hasattr(rich_text, "plain") else str(rich_text)


def test_renderers_name_the_actual_actors():
    lines = [_plain(l) for l in activity_lines(EVENTS)]
    assert any("PLANNER" in l and "droid/gpt-6-sol" in l and "7.0k credits" in l for l in lines)
    assert any("ROUTER" in l and "Laya (laya_mlx" in l and "tier 0 droid/glm-5.3-flash" in l for l in lines)
    assert any("WORKER" in l and "$ echo hello > /app/hello.txt" in l for l in lines)
    assert any("VERIFIER" in l and "PASS" in l for l in lines)
    roles = _plain(roles_text(EVENTS))
    assert "PLANNER  droid/gpt-6-sol" in roles and "Laya (laya_mlx, local MLX) ×1" in roles
    assert "droid/glm-5.3-flash ×1" in roles and "2:droid/gpt-6-sol (escalation)" in roles
    assert step_rows(EVENTS)[0][3:6] == ("droid/glm-5.3-flash", "glm-5.3-flash", "PASS")


def test_job_statuses(tmp_path):
    make_job(tmp_path, "ok")
    make_job(tmp_path, "killed", reward=None, exception={"exception_type": "CancelledError"})
    make_job(tmp_path, "broke", reward=None, exception={"exception_type": "RuntimeError", "exception_message": "x"})
    (tmp_path / "jobs" / "fresh.run.json").write_text(json.dumps({"pid": os.getpid(), "task": "t",
                                                                   "cmd": ["config=c.json", "policy=laya"],
                                                                   "started": time.time()}))
    jobs = {j.name: j for j in J.list_jobs(tmp_path / "jobs")}
    assert jobs["ok"].status == "passed" and jobs["ok"].reward == 1.0 and jobs["ok"].caps == {"max_plan_steps": 5}
    assert jobs["killed"].status == "stopped" and jobs["broke"].status == "error"
    assert jobs["fresh"].status == "running" and jobs["fresh"].policy == "laya"
    assert next(iter(J.list_jobs(tmp_path / "jobs"))).name == "fresh"  # newest first


def test_build_and_start_command(tmp_path):
    smoke = J.build_command(J.SMOKE_TASK, "configs/phase2-droid.json", "laya", {"max_plan_steps": "5"}, "j1")
    assert smoke[:2] == ["caffeinate", "-i"] and ["-p", J.SMOKE_TASK] == smoke[4:6]
    assert "max_plan_steps=5" in smoke and smoke[-2:] == ["--job-name", "j1"]
    tb = J.build_command("terminal-bench/shadow-relay", "c", "laya", {}, "j2")
    assert ["-d", J.TB_DATASET, "-i", "terminal-bench/shadow-relay"] == tb[4:8]

    seen = {}

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return FakeProc()

    assert J.start_run(tmp_path, J.SMOKE_TASK, "c", "laya", {}, "j3", popen=fake_popen) == 4242
    assert seen["start_new_session"] and json.loads((tmp_path / "jobs" / "j3.run.json").read_text())["pid"] == 4242
    (tmp_path / "jobs" / "j4").mkdir()
    try:
        J.start_run(tmp_path, J.SMOKE_TASK, "c", "laya", {}, "j4", popen=fake_popen)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_stop_run_kills_group_and_removes_containers(tmp_path):
    job_dir = make_job(tmp_path, "live", reward=None)
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    (tmp_path / "jobs" / "live.run.json").write_text(json.dumps({"pid": p.pid, "started": time.time()}))
    job = J.load_job(job_dir)
    docker = []

    def fake_run(cmd, **kw):
        docker.append(cmd)
        out = "c1 c2" if cmd[:2] == ["docker", "ps"] else ("n1" if cmd[1] == "network" and cmd[2] == "ls" else "")
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    done = J.stop_run(job, run=fake_run, grace_sec=2)
    p.wait(timeout=5)
    assert p.returncode is not None and "removed 2 container(s)" in done and "removed 1 network(s)" in done
    assert ["docker", "rm", "-f", "c1", "c2"] in docker
    assert any("name=hello-file__abc123" in " ".join(c) for c in docker)
    assert (tmp_path / "jobs" / "live.stopped").exists()


def test_tui_shows_job_and_starts_a_run(tmp_path):
    make_job(tmp_path, "demo")
    (tmp_path / "configs").mkdir()
    launched = []

    def launcher(root, task, config, policy, caps, job_name):
        launched.append((task, config, policy, caps, job_name))
        return 999

    async def main():
        app = HarnessTUI(root=tmp_path, launcher=launcher, stopper=lambda job: [], poll_sec=60)
        import phase2.tui as tui_mod
        tui_mod.ROOT = Path(__file__).resolve().parents[1]  # real configs/ for the new-run form
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            assert len(app.query_one("#jobs").children) == 1
            tree = app.query_one("#overview")
            top = [n.label.plain for n in tree.root.children]
            assert top[0].startswith("Planner: GPT-6 Sol") and top[1] == "✔ make hello.txt"
            assert any("Router: Laya" in n.label.plain for n in tree.root.children[1].children)
            assert "Laya (laya_mlx" in app.query_one("#roles").render().plain
            await pilot.press("n")
            await pilot.pause()
            await pilot.click("#start")
            await pilot.pause()
        return launched

    launched = asyncio.run(main())
    assert launched and launched[0][0] == J.SMOKE_TASK and launched[0][2] == "laya"
    assert launched[0][3] == {"max_plan_steps": "5", "worker_step_limit": "25", "step_idle_sec": "600"}


def test_story_reads_like_a_log_of_who_did_what():
    from phase2.tui import describe_command, pretty_model, story_lines, summarize_output

    assert pretty_model("droid/gpt-6-sol") == "GPT-6 Sol (via Droid)"
    assert pretty_model("droid/deepseek-v4.1-flash") == "DeepSeek V4.1 Flash (via Droid)"
    assert pretty_model("claude/claude-opus-5-5") == "Claude Opus 5.5 (via Claude Code)"
    assert describe_command("cd /app && head -5 a.log && wc -l a.log") == "read the start of a.log"
    assert describe_command("printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\nok'") == "declared the step done"
    assert summarize_output("<returncode>0</returncode>\n<output>\ntotal 4\nREADME\n</output>")[1] == "ok · 1 lines · README"
    assert summarize_output("<returncode>127</returncode>\n<output>\nxxd: not found\n</output>")[0] is False
    workers = {"worker_s1_a1": [{"role": "system", "content": "s"}, {"role": "user", "content": "task"},
                                {"role": "assistant", "content": "THOUGHT: look first\n\n```mswea_bash_command\nls /app\n```"},
                                {"role": "user", "content": "<returncode>0</returncode>\n<output>\na.txt\n</output>"}]}
    story = "\n".join(l.plain for l in story_lines(EVENTS, workers))
    assert "PLANNER GPT-6 Sol (via Droid)" in story and "Split the task into 1 step:" in story
    assert "ROUTER Laya  judged it easy (0.40 of 2, 30% sure) → sent it to GLM 5.3 Flash (via Droid)" in story
    assert "1. listed files" in story and "thinking: look first" in story and "$ ls /app" in story
    assert "→ ok · 1 lines · a.txt" in story and "VERIFIER task container  ran `test -f x` → passed ✔" in story


def test_overview_tree_is_plain_words():
    from phase2.tui import first_sentence, overview

    assert first_sentence("Logs have odd names (e.g. abc.cc). Next I group them.") == "Logs have odd names (e.g. abc.cc)"
    workers = {"worker_s1_a1": [{"role": "system", "content": "s"}, {"role": "user", "content": "task"},
                                {"role": "assistant", "content": "THOUGHT: Look first. Then act.\n```mswea_bash_command\nls /app\n```"},
                                {"role": "user", "content": "<returncode>0</returncode>\n<output>\na.txt\n</output>"},
                                {"role": "assistant", "content": "```mswea_bash_command\nfor f in *; do echo $f; done\n```"}]}
    rows = overview(EVENTS, workers, {"verifier_result": {"rewards": {"reward": 1.0}}})

    def flat(items, depth=0):
        for _, label, children in items:
            yield "  " * depth + label.plain
            yield from flat(children, depth + 1)

    lines = list(flat(rows))
    assert lines[0] == "Planner: GPT-6 Sol (via Droid) split the task into 1 step"
    assert lines[1] == "✔ make hello.txt"
    assert "  Router: Laya rated it easy and gave it to GLM 5.3 Flash (via Droid)" in lines
    assert "  Worker: GLM 5.3 Flash (via Droid)" in lines
    assert "    Listed files — Look first" in lines and "    Looped over items  … working" in lines
    assert "  Check: the step's test passed" in lines
    assert lines[-1].startswith("Result: task solved ✔")


def test_overview_shows_escalation_between_attempts():
    from phase2.tui import overview

    ev = EVENTS[:5] + [
        {"t": 15.0, "event": "attempt", "step": "s1", "attempt": 1, "tier": 0, "model": "droid/glm-5.3-flash",
         "exit_status": "TimeExceeded", "n_calls": 1, "passed": False, "check": ""},
        {"t": 15.1, "event": "escalate", "step": "s1", "from_tier": 0, "to_tier": 1},
        {"t": 15.2, "event": "handoff", "step": "s1", "chars": 900}]
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "t"},
            {"role": "assistant", "content": "```mswea_bash_command\nls\n```"}]
    rows = overview(ev, {"worker_s1_a1": msgs, "worker_s1_a2": msgs})
    kids = [label.plain for _, label, _ in rows[1][2]]
    assert kids[1] == "Worker: GLM 5.3 Flash (via Droid)"
    assert kids[3].startswith("Escalated: the first attempt did not pass, so the step goes to DeepSeek V4.1 Flash")
    assert kids[3].endswith("with the first attempt's notes")
    assert kids[4] == "Worker: DeepSeek V4.1 Flash (via Droid) (second try, one tier up)"


def test_stop_run_also_kills_model_calls_in_their_own_groups(tmp_path):
    ps = "100 1 100\n101 100 100\n102 101 102\n103 102 102\n200 1 200\n"
    assert J.descendant_groups(100, ps) == {100, 102}
    job_dir = make_job(tmp_path, "tree", reward=None)
    # run (own group) -> child -> "model call" started in its own session, like run_group does
    code = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
            "start_new_session=True); time.sleep(60)")
    p = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    time.sleep(0.8)
    call_groups = J.descendant_groups(p.pid) - {p.pid}
    assert call_groups
    (tmp_path / "jobs" / "tree.run.json").write_text(json.dumps({"pid": p.pid, "started": time.time()}))
    J.stop_run(J.load_job(job_dir), run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""), grace_sec=2)
    p.wait(timeout=5)
    time.sleep(0.3)
    for g in call_groups:
        try:
            os.killpg(g, 0)
            raise AssertionError(f"model call group {g} still alive")
        except ProcessLookupError:
            pass


def test_progress_panel_plain_status():
    from phase2.tui import progress_panel

    events = [
        {"t": 0.0, "event": "config", "tiers": ["droid/glm-5.3-flash", "droid/glm-5.3", "droid/gpt-6-sol"],
         "worker_step_limit": 25, "step_wall_time_sec": 900, "step_idle_sec": 600, "planner_model": "droid/gpt-6-sol"},
        {"t": 10.0, "event": "plan", "steps": [
            {"id": "s1", "goal": "Find the host. More words.", "depends_on": [], "writes": [], "check": ""},
            {"id": "s2", "goal": "Decode the session.", "depends_on": [], "writes": [], "check": ""},
            {"id": "s3", "goal": "Write the flag.", "depends_on": ["s1", "s2"], "writes": [], "check": "test -f x"}]},
        {"t": 11.0, "event": "route", "step": "s1", "tier": 0, "model": "droid/glm-5.3-flash"},
        {"t": 11.0, "event": "route", "step": "s2", "tier": 0, "model": "droid/glm-5.3-flash"},
        {"t": 20.0, "event": "llm_call", "role": "worker", "step": "s1", "model": "droid/glm-5.3-flash", "ms": 9000},
        {"t": 30.0, "event": "attempt", "step": "s1", "attempt": 1, "model": "droid/glm-5.3-flash", "passed": True,
         "n_calls": 1},
        {"t": 40.0, "event": "attempt", "step": "s2", "attempt": 1, "model": "droid/glm-5.3-flash", "passed": False,
         "n_calls": 3},
        {"t": 40.0, "event": "escalate", "step": "s2", "from_tier": 0, "to_tier": 1},
        {"t": 50.0, "event": "llm_call_failed", "role": "worker", "step": "s2", "model": "droid/glm-5.3",
         "kind": "timeout", "ms": 180000, "will_retry": True},
    ]
    text = progress_panel(events, now_t=400.0).plain
    assert "1 of 3 steps done" in text and "running for 6m 40s" in text
    assert "done by GLM 5.3 Flash (via Droid)" in text
    assert "retrying with a stronger model: GLM 5.3 (via Droid) · action 1 of 25" in text
    assert "waiting for GLM 5.3's next move: 5m 50s" in text and "1 failed model call(s)" in text
    assert "waits for steps 1 and 2 to finish" in text

    ended = progress_panel(events, now_t=400.0, status="stopped").plain
    assert "stopped mid-work" in ended and "ran for" in ended


def test_progress_panel_before_plan():
    from phase2.tui import progress_panel

    assert "Starting up" in progress_panel([], 0).plain
    cfg = [{"t": 0.0, "event": "config", "planner_model": "droid/gpt-6-sol"}]
    assert "Planning: GPT-6 Sol (via Droid)" in progress_panel(cfg, 12).plain
