"""Relay: the web app for the Phase 2 routed harness.

  .venv/bin/python -m uvicorn app.server.main:app --host 127.0.0.1 --port 8570

Reads jobs/ (traces, Harbor results) and starts runs only through phase2.jobs.start_run.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("RELAY_ROOT", Path(__file__).resolve().parents[2]))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from phase2 import jobs as J  # noqa: E402

from . import benchmarks, connections, launch, live, runs  # noqa: E402
from .models import LIVE_SETUPS  # noqa: E402

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    setup: str = "ocxv4"


class RerunIn(BaseModel):
    source_job: str
    setup: str = "ocxv4"


def create_app(root: Path = ROOT, launcher=None, stopper=None, poll_sec: float = 0.5) -> FastAPI:
    app = FastAPI(title="Relay", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.root = root
    app.state.launcher = launcher or J.start_run
    app.state.stopper = stopper or J.stop_run

    def job_exists(name: str) -> None:
        try:
            runs.safe_name(name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        jobs = app.state.root / "jobs"
        if not (jobs / name).exists() and not (jobs / f"{name}.run.json").exists():
            raise HTTPException(404, f"no job named {name}")

    @app.get("/api/health")
    def health():
        return {"ok": True, "root": str(app.state.root)}

    @app.get("/api/setups")
    def setups():
        return {"setups": LIVE_SETUPS, "default": "ocxv4"}

    @app.get("/api/history")
    def history(limit: int = 40):
        return {"runs": runs.list_history(app.state.root, limit=min(max(limit, 1), 200))}

    @app.get("/api/runs/{name}")
    def run_snapshot(name: str):
        job_exists(name)
        return runs.snapshot(app.state.root, name)

    @app.get("/api/runs/{name}/stream")
    async def run_stream(name: str, request: Request):
        """SSE: the whole run so far (after a `reset`), then new events as the trace grows, until the result."""
        job_exists(name)
        root = app.state.root

        async def gen():
            tail = runs.LiveTail(root, name)
            yield _sse({"type": "reset", "run": runs.job_summary(root, name),
                        "instruction": runs.instruction(root, name, runs.trial_dir(root / "jobs" / name))})
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    return
                events = await asyncio.to_thread(tail.poll)
                for ev in events:
                    yield _sse(ev)
                if tail.finished:
                    yield _sse({"type": "end"})
                    return
                idle += poll_sec
                if idle >= 10:
                    idle = 0.0
                    yield ": keep-alive\n\n"
                await asyncio.sleep(poll_sec)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/chat")
    def chat(body: ChatIn):
        try:
            return launch.start_chat(app.state.root, body.text, body.setup, app.state.launcher)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except OSError as e:
            raise HTTPException(500, f"could not start the run: {e}")

    @app.post("/api/rerun")
    def rerun(body: RerunIn):
        job_exists(body.source_job)
        try:
            return launch.start_rerun(app.state.root, body.source_job, body.setup, app.state.launcher)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except OSError as e:
            raise HTTPException(500, f"could not start the run: {e}")

    @app.post("/api/runs/{name}/stop")
    def stop(name: str):
        job_exists(name)
        if not name.startswith(("chat-", "app-")):
            raise HTTPException(403, "the app only stops runs it started (chat-*, app-*)")
        job = J.load_job(app.state.root / "jobs" / name)
        if job.status not in ("running",):
            raise HTTPException(409, f"run is {job.status}, not running")
        return {"done": app.state.stopper(job)}

    @app.get("/api/connections")
    def conns(setup: str = "ocxv4", fresh: bool = False):
        if setup not in launch.LIVE_IDS:
            raise HTTPException(400, "unknown setup")
        return connections.connections(app.state.root, setup, fresh=fresh)

    @app.get("/api/live")
    def live_now():
        """Running jobs (live pid), progress per active evaluation, GLM quota. Poll every 10-15 s."""
        return JSONResponse(live.live(app.state.root))

    @app.get("/api/benchmarks")
    def evals():
        return {"evaluations": benchmarks.evaluations(app.state.root)}

    @app.get("/api/benchmarks/{prefix}")
    def evaluation(prefix: str):
        if not prefix.replace("_", "").isalnum():
            raise HTTPException(400, "bad prefix")
        return JSONResponse(benchmarks.evaluation(app.state.root, prefix))

    if WEB_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "unknown API path")
            f =(WEB_DIST / path).resolve()
            if path and f.is_file() and WEB_DIST.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(WEB_DIST / "index.html")

    return app


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, default=str)}\n\n"


app = create_app()
