"""Relay server: trace normalization, run listing, replay/stream endpoints, launches (start_run mocked).
No network, no Docker, no model calls."""
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402

from app.server import connections, launch, runs  # noqa: E402
from app.server.main import create_app  # noqa: E402
from app.server.models import model_label  # noqa: E402
from app.server.normalize import Normalizer, call_cost, count_checks, normalize, reported_files  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
DRY = "dry1-ocxv4-hello-file"
TAKEOVER = "dv9-takeover28-demo-task"


@pytest.fixture
def root(tmp_path):
    shutil.copytree(FIX / "jobs", tmp_path / "jobs")
    (tmp_path / "configs").mkdir()
    for c in ("phase2-ocx-v4.json", "phase2-ocx-base.json", "phase2-ocx-takeover28.json"):
        shutil.copy(REPO / "configs" / c, tmp_path / "configs" / c)
    return tmp_path


def rows(job):
    td = next(p for p in (FIX / "jobs" / job).iterdir() if p.is_dir())
    return [json.loads(l) for l in (td / "agent" / "trace.jsonl").read_text().splitlines() if l.strip()]


def of(events, type_):
    return [e for e in events if e["type"] == type_]


# ---------- labels and small helpers ----------

def test_model_labels():
    assert model_label("ocx/zai/glm-5.3-flash") == "GLM-5.3 Flash"
    assert model_label("ocx/anthropic/claude-opus-5-5") == "Opus 5.5"
    assert model_label("droid/claude-sonnet-5-5") == "Sonnet 5.5"
    assert model_label("droid/glm-5.3-flash") == "GLM-5.3 Flash"


def test_cost_prefers_trace_then_list_price():
    assert call_cost({"model": "ocx/zai/glm-5.3-flash", "cost_usd": 0.5}) == (0.5, "trace")
    usd, src = call_cost({"model": "droid/glm-5.3-flash", "cost_usd": 0.0, "input_tokens": 1_000_000,
                          "cached_tokens": 0, "output_tokens": 0})
    assert src == "list_price" and usd == pytest.approx(0.15)
    assert call_cost({"model": "x/unknown", "cost_usd": 0}) == (0.0, "unpriced")


def test_checks_count_only_the_output_section():
    text = "--- script\necho \"PASS a\"\necho \"FAIL a\"\n--- output\nPASS a\nPASS b\n--- exit code: 0\n"
    assert count_checks({"text": text, "passes": 9, "fails": 9}) == (2, 0)
    assert count_checks({"passes": 3, "fails": 1}) == (3, 1)


def test_reported_files_skip_tmp():
    assert reported_files({"a": "/app/x.py", "b": "/tmp/notes", "c": "hello"}) == ["/app/x.py"]
    assert reported_files({"a": "(see /app/POLICY.md)", "b": "/app/out.csv."}) == ["/app/POLICY.md", "/app/out.csv"]


# ---------- normalization ----------

def test_v4_run_phases_and_narration():
    ev = normalize(rows(DRY))
    starts = [e["phase"]["kind"] for e in of(ev, "phase_start")]
    assert starts == ["build", "checks", "review"]
    texts = [e["text"] for e in of(ev, "narration")]
    assert "GLM-5.3 Flash is building." in texts
    assert "Checks: 4 passed, 0 failed." in texts  # recounted from the output, not the script text (8/4 logged)
    assert "Opus 5.5 reviewed: ship." in texts
    ends = {e["phase_id"]: e for e in of(ev, "phase_end")}
    build = next(e["phase"] for e in of(ev, "phase_start") if e["phase"]["kind"] == "build")
    assert ends[build["id"]]["ended"] == 61.17  # the builder's last reply, not when the next event arrived
    assert [e["seq"] for e in ev] == list(range(1, len(ev) + 1))


def test_usage_totals_match_trace():
    ev = normalize(rows(DRY))
    last = of(ev, "usage")[-1]
    total_cost = sum(r.get("cost_usd") or 0 for r in rows(DRY) if r["event"] == "llm_call")
    assert last["total"]["cost_usd"] == pytest.approx(total_cost)
    assert last["total"]["calls"] == 4
    assert [m["label"] for m in last["by_model"]] == ["Opus 5.5", "GLM-5.3 Flash"]  # biggest spend first


def test_working_now_follows_handoffs():
    ev = normalize(rows(TAKEOVER))
    who = [[w["label"] + " " + w["kind"] for w in e["now"]] for e in of(ev, "working")]
    assert who[1:] == [["GLM-5.3 Flash build"], ["Opus 5.5 review"], [], ["Opus 5.5 takeover"], []]


def test_takeover_run_review_and_summary():
    ev = normalize(rows(TAKEOVER))
    review = of(ev, "review")[0]
    assert review["verdict"] == "fix" and review["problems"] == ["Empty input still crashes",
                                                                 "Unicode names are dropped"]
    texts = [e["text"] for e in of(ev, "narration")]
    assert "Opus 5.5 reviewed: fix (2 problems)." in texts
    assert "Handoff: Opus 5.5 takes over to fix 2 problems." in texts
    assert "A call to Opus 5.5 failed (transient), retrying." in texts
    step = of(ev, "step_end")[0]
    assert step["summary"].startswith("Handled empty input")  # the takeover's summary wins
    assert step["files"] == ["/app/parser.py"]
    assert of(ev, "activity")[0]["text"] == "read-only check: cat parser.py"
    # Droid logged cost 0: list prices fill in
    assert all(e["cost_source"] == "list_price" for e in of(ev, "model_call"))
    assert of(ev, "usage")[-1]["total"]["cost_usd"] > 0


def test_streaming_feed_equals_batch():
    """Feeding rows one by one (live) gives the same events as a recorded replay."""
    n = Normalizer()
    live = []
    for r in rows(TAKEOVER):
        live += n.feed(r)
    assert live == normalize(rows(TAKEOVER))


def test_unknown_events_are_ignored():
    ev = normalize([{"t": 0, "event": "something_new", "x": 1}, {"t": 1, "event": "snapshot"}])
    assert of(ev, "narration") == []


# ---------- run listing and snapshots ----------

def test_history_lists_jobs_and_chat_runs(root):
    (root / "jobs" / "_adhoc").mkdir()
    (root / "jobs" / "_adhoc" / "1118x.chat.json").write_text(json.dumps({"text": "make a todo app"}))
    (root / "jobs" / "chat-ocxv4-1118x.run.json").write_text(json.dumps(
        {"pid": 999999999, "cmd": [], "task": "jobs/_adhoc/1118x", "started": 2e9}))
    hist = runs.list_history(root)
    names = [r["job"] for r in hist]
    assert names[0] == "chat-ocxv4-1118x" and set(names) == {"chat-ocxv4-1118x", DRY, TAKEOVER}
    chat = hist[0]
    assert chat["kind"] == "chat" and chat["title"] == "make a todo app" and chat["grader"] is False
    dry = next(r for r in hist if r["job"] == DRY)
    assert dry["status"] == "passed" and dry["setup_label"] == "v4"


def test_snapshot_has_result_and_instruction(root):
    snap = runs.snapshot(root, TAKEOVER)
    res = snap["events"][-1]
    assert res["type"] == "result" and res["reward"] == 0.0 and res["tests"] == 0.75 and res["grader"]
    assert snap["instruction"].startswith("Fix the parser")


# ---------- API ----------

def client(root, launcher=None):
    return TestClient(create_app(root, launcher=launcher, poll_sec=0.01))


def test_api_replay_and_404(root):
    c = client(root)
    r = c.get(f"/api/runs/{DRY}")
    assert r.status_code == 200 and r.json()["events"][-1]["type"] == "result"
    assert c.get("/api/runs/nope-x-y").status_code == 404
    assert c.get("/api/runs/..%2Fetc").status_code in (400, 404)


def test_api_stream_of_finished_run(root):
    c = client(root)
    with c.stream("GET", f"/api/runs/{TAKEOVER}/stream") as r:
        body = "".join(r.iter_text())
    events = [json.loads(l[6:]) for l in body.splitlines() if l.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "reset" and types[-1] == "end" and "result" in types and "takeover" not in types
    assert any(e["type"] == "narration" and "takes over" in e["text"] for e in events)


def test_api_benchmarks(root):
    c = client(root)
    evals = c.get("/api/benchmarks").json()["evaluations"]
    assert {e["prefix"] for e in evals} == {"dry1", "dv9"}
    d = c.get("/api/benchmarks/dv9").json()
    assert d["setups"][0]["setup"] == "takeover28" and d["setups"][0]["passed"] == 0
    assert d["tasks"][0]["runs"][0]["tests"] == 0.75


def test_api_chat_creates_adhoc_task_and_calls_start_run(root):
    calls = []

    def fake_start(root_, task, config, policy, caps, job):
        calls.append((task, config, policy, caps, job))
        return 4242

    c = client(root, launcher=fake_start)
    r = c.post("/api/chat", json={"text": "Write hello.py that prints hi", "setup": "ocxv4"})
    assert r.status_code == 200, r.text
    out = r.json()
    task, config, policy, caps, job = calls[0]
    assert job == out["job"] and job.startswith("chat-ocxv4-")
    assert (config, policy) == ("configs/phase2-ocx-v4.json", "fixed:1") and caps["max_plan_steps"] == "1"
    tdir = root / task
    assert (tdir / "instruction.md").read_text() == "Write hello.py that prints hi\n"
    assert "FROM python:3.12-slim" in (tdir / "environment" / "Dockerfile").read_text()
    assert "echo 1 > /logs/verifier/reward.txt" in (tdir / "tests" / "test.sh").read_text()
    assert (tdir / "task.toml").exists()
    meta = json.loads((root / "jobs" / "_adhoc" / f"{out['id']}.chat.json").read_text())
    assert meta["setup"] == "ocxv4" and meta["job"] == job
    assert c.post("/api/chat", json={"text": "x", "setup": "takeover28"}).status_code == 400
    assert c.post("/api/chat", json={"text": "", "setup": "ocxv4"}).status_code == 422


def test_api_rerun_names_app_jobs(root):
    calls = []
    c = client(root, launcher=lambda *a: calls.append(a) or 1)
    r = c.post("/api/rerun", json={"source_job": TAKEOVER, "setup": "ocxglm"})
    assert r.status_code == 200, r.text
    _, task, config, policy, caps, job = calls[0]
    assert task == "terminal-bench/demo-task" and job == "app-ocxglm-demo-task-r1"
    assert (config, policy) == ("configs/phase2-ocx-base.json", "fixed:1")
    (root / "jobs" / "app-ocxglm-demo-task-r1").mkdir()
    c.post("/api/rerun", json={"source_job": TAKEOVER, "setup": "ocxglm"})
    assert calls[1][-1] == "app-ocxglm-demo-task-r2"
    local = c.post("/api/rerun", json={"source_job": DRY, "setup": "ocxopus"})
    assert local.status_code == 200 and calls[2][1] == "tests/harbor_smoke/hello-file"


def test_api_stop_only_app_runs(root):
    c = client(root)
    assert c.post(f"/api/runs/{DRY}/stop").status_code == 403


def test_connections_green_amber_red(root):
    now = __import__("time").time()
    probes = {"ocx": {"ok": True, "ids": ["zai/glm-5.3-flash", "anthropic/claude-opus-5-5"], "ms": 3},
              "docker": {"ok": False, "error": "Cannot connect"},
              "quota": (12.0, 40.0),
              "activity": {"ocx/zai/glm-5.3-flash": {"last_ok": now - 60, "last_fail": None, "last_error": ""},
                           "ocx/anthropic/claude-opus-5-5": {"last_ok": now - 7200, "last_fail": now - 30,
                                                             "last_error": "overloaded"}}}
    out = connections.connections(root, "ocxv4", probes=probes)
    by = {i["id"]: i for i in out["items"]}
    assert by["ocx"]["status"] == "green"
    assert by["model:ocx/zai/glm-5.3-flash"]["status"] == "green"
    assert by["model:ocx/zai/glm-5.3-flash"]["role"] == "builder"
    assert by["model:ocx/anthropic/claude-opus-5-5"]["status"] == "amber"
    assert by["model:ocx/anthropic/claude-opus-5-5"]["role"] == "judge + takeover"
    assert by["docker"]["status"] == "red" and out["overall"] == "red"
    assert "weekly 40%" in by["zai"]["detail"]
    down = connections.connections(root, "ocxglm", probes={**probes, "ocx": {"ok": False, "ids": [], "error": "refused"},
                                                           "quota": None})
    by = {i["id"]: i for i in down["items"]}
    assert by["ocx"]["status"] == "red" and by["model:ocx/zai/glm-5.3-flash"]["status"] == "red"
    assert by["zai"]["status"] == "amber" and "opus" not in " ".join(by)


def test_recent_model_activity_reads_traces(root):
    act = connections.recent_model_activity(root)
    assert act["droid/claude-opus-5-5"]["last_fail"] and act["droid/claude-opus-5-5"]["last_error"] == "overloaded"
    assert act["ocx/zai/glm-5.3-flash"]["last_ok"]


def test_setup_args_match_setups():
    assert launch.setup_args("ocx28") == ("configs/phase2-ocx-takeover28.json", "fixed:1",
                                          launch.setup_args("ocxglm")[2])


# ---------- running now ----------

def _fake_running(root, name, task_dir=None):
    """A job whose launcher pid is this test process (alive), optionally with a growing trace."""
    import os
    (root / "jobs" / f"{name}.run.json").write_text(json.dumps(
        {"pid": os.getpid(), "cmd": ["--ak", "config=configs/phase2-ocx-v4.json"], "task": "terminal-bench/x",
         "started": __import__("time").time() - 90}))
    if task_dir:
        td = root / "jobs" / name / task_dir / "agent"
        td.mkdir(parents=True)
        src = next(p for p in (FIX / "jobs" / DRY).iterdir() if p.is_dir()) / "agent" / "trace.jsonl"
        lines = src.read_text().splitlines()[:6]  # config, plan, snapshot, route, two worker calls
        (td / "trace.jsonl").write_text("\n".join(lines) + "\n")


def test_live_lists_running_jobs_with_who_and_cost(root):
    from app.server import live
    _fake_running(root, "v4d9-ocxglm-demo-task-r1", task_dir="demo-task__Zz")
    _fake_running(root, "app-ocxopus-hello-file-r1")
    out = live.live(root, quota=(10.0, 42.0))
    by = {r["job"]: r for r in out["running"]}
    assert set(by) == {"v4d9-ocxglm-demo-task-r1", "app-ocxopus-hello-file-r1"}
    v = by["v4d9-ocxglm-demo-task-r1"]
    assert v["setup_label"] == "GLM alone" and v["task"] == "demo-task" and v["stage"] == "running"
    assert v["working"][0]["label"] == "GLM-5.3 Flash" and v["phase"] == "build"
    assert v["tokens"] > 0 and v["cost_usd"] > 0 and v["elapsed"] >= 89
    a = by["app-ocxopus-hello-file-r1"]
    assert a["setup_label"] == "Opus alone" and a["stage"] == "starting" and a["kind"] == "rerun"
    assert {b["prefix"] for b in out["batches"]} == {"v4d9", "app"}
    assert out["quota"] == {"five_hour": 10.0, "weekly": 42.0}
    # finished jobs (dry1, dv9) are not listed
    assert DRY not in by


def test_live_batch_progress_and_api(root):
    from app.server import live
    (root / "jobs" / "dv9.plan.json").write_text(json.dumps({"tasks": ["demo-task", "other"], "setups": ["takeover28"],
                                                              "repeats": 1, "parallel": 2}))
    b = live.batch_progress(root, "dv9")
    assert (b["total"], b["done"], b["running"], b["queued"]) == (2, 1, 0, 1)
    assert b["setups"][0]["label"] == "v2.8 (Droid)" and b["setups"][0]["passed"] == 0 and b["usd"] > 0
    assert b["eta"] is not None
    _fake_running(root, "dv9-takeover28-other", task_dir="other__Yy")
    r = client(root).get("/api/live")
    assert r.status_code == 200
    body = r.json()
    assert [x["job"] for x in body["running"]] == ["dv9-takeover28-other"]
    assert body["batches"][0]["prefix"] == "dv9" and body["batches"][0]["running"] == 1


def test_live_empty(root):
    from app.server import live
    out = live.live(root, quota=(1.0, 2.0))
    assert out["running"] == [] and out["batches"] == []
