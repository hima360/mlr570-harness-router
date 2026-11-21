# Relay

Relay is the web app for the MLR570 Phase 2 routed harness. The models hand work to each other like runners in a
relay. You type a task the way you would in ChatGPT or Claude. You then watch, live, which model is working, what
each one produced, the tokens it used, the estimated API cost, and which providers are connected. A separate
Benchmarks view replays recorded Harbor runs step by step with their verifier scores, or reruns one of their tasks.

The app only reads what the harness already writes (`jobs/<job>/<trial>/agent/trace.jsonl` and Harbor's
`result.json`). It starts runs only through `phase2.jobs.start_run`, using a setup from `phase2/setups.py`. It
never changes harness behaviour, configs or prices. Cost is the trace's own `llm_call.cost_usd` (tokens x list
price). It is always labelled "estimated API cost" and is never a bill. Factory Droid runs logged a cost of 0, so
their tokens are priced with `phase2.list_prices`, the same way `phase2.results` prices them.

## Run it

```bash
app/run.sh                 # builds the web app if its sources changed, then serves http://127.0.0.1:8570
app/run.sh --rebuild       # force a fresh front-end build
RELAY_PORT=9000 app/run.sh
```

`run.sh` needs the repo's `.venv` (FastAPI and uvicorn, see `app/requirements.txt`) and Node 20 or later for the
first build.

Development, with hot reload:

```bash
.venv/bin/python -m uvicorn app.server.main:app --port 8570 --reload   # API
cd app/web && npm install && npm run dev                               # http://localhost:5173, proxies /api to :8570
```

Tests (no network, no Docker, no model calls; `start_run` is mocked):

```bash
.venv/bin/python -m pytest app/server/tests -q
```

## Views

- **Chat.** Type a task in plain language and press Enter. A confirm bar names the setup and says the run uses
  real quota; press Enter again to start or Esc to cancel. Pick the setup next to the task box: v4 (`ocxv4`,
  default), v2.8 (`ocx28`), Opus alone (`ocxopus`) or GLM alone (`ocxglm`). The server writes an ad-hoc Harbor
  task to `jobs/_adhoc/<id>/`, shaped like `tests/harbor_smoke/hello-file`. It has a Debian Python 3.12 image with
  common tools, and a `tests/test.sh` that always writes reward 1. It then starts job `chat-<setup>-<id>`. Relay's
  reply tells the story in plain words ("GLM-5.3 Flash is building", "Checks: 4 passed, 0 failed", "Opus 5.5
  reviewed: ship", "Handoff: Opus 5.5 takes over to fix 2 problems"). It ends with a result card: what was done,
  the files the builder reported, total tokens, estimated cost and wall time. Chat runs are labelled
  "no benchmark grader" because their verifier always passes.
- **Task panel (right).** Who's working now, with a live timer. Below it are three tabs.
  - **Tasks:** each plan step and its phases (build, checks, review, takeover, builder fix), with status, model,
    tokens, cost and timer. Open a phase to see each model call and the start of its reply, the check output,
    or the reviewer's read-only commands. The judge's verdict and problems are listed under the steps.
  - **Cost:** tokens in, cached and out, plus estimated cost, per model and in total. It updates live.
  - **Connections:** each check is green, amber or red, with a plain reason. They are: the opencodex proxy
    (`/v1/models`), each model in the setup (whether opencodex lists it, and its last success or failure in recent
    traces), the Z.ai quota (5-hour and weekly, via `phase2.quota.read_zai_quota`, cached 5 minutes) and Docker.
    None of these checks spends model quota.
- **Benchmarks.** Lists every evaluation prefix in `jobs/`. For the chosen one it shows a per-setup table from
  `phase2.results` (runs, solved, mean share of tests, total estimated cost, cost per solve, tokens, minutes per
  run) and a task-by-setup grid. Click a run to replay it as a **recording**: play or pause, five speeds, scrub,
  step between moments, and Harbor's verifier result at the end (reward and share of tests). Long idle gaps are
  skipped so a 40-minute run replays in a minute or two. **Rerun this task** starts a fresh Harbor run of the same
  task with a chosen setup, as job `app-<setup>-<task>-r<n>`, and streams it live.
- **Running now (left, top).** Every job under `jobs/` whose process is alive, from any prefix (batches like
  `v4d2-*`, `app-*` reruns, `chat-*`). Each row shows the setup, the task, elapsed time, who is working (or the
  current phase), and tokens and estimated cost so far; click it to open the live view. Above the list are a
  progress card per active evaluation (done / running / queued, solves per setup, estimated cost, finish ETA, from
  `phase2.results.progress`) and the GLM quota (5-hour and weekly). It refreshes every 12 s from `GET /api/live`.
  That endpoint makes no model calls and re-reads a trace only when it has grown.
- **History (left).** Your chats, your reruns, and the most recent finished harness runs, each with its status.

Keys: `N` new task, `B` benchmarks, `/` or `Cmd+K` focus the task box, `Space` play or pause, `Left`/`Right` step,
`[`/`]` speed, `Home`/`End`, `T` theme (system, light, dark), `P` presentation size (larger type for a projector),
`?` for the list.

## How it is built

- `server/` is FastAPI.
  - `normalize.py` turns raw trace rows into typed UI events: `phase_start`/`phase_end`, `model_call`, `checks`,
    `review`, `narration`, `usage`, `working`, `result` and others. It is one stateful normalizer, used for both
    the live stream and replays, so a replay shows exactly what the live view showed.
  - `runs.py` lists jobs and tails a running trace.
  - `launch.py` creates ad-hoc tasks and calls `start_run`.
  - `connections.py` runs the connection checks, and `benchmarks.py` wraps `phase2.results`.
  - Live runs stream over SSE at `GET /api/runs/<job>/stream`. The stream sends a `reset`, then the whole run so
    far, then new events as the trace grows, and ends with the verifier `result`.
- `web/` is React, TypeScript and Vite. The chat thread is assistant-ui (`useExternalStoreRuntime`, with the run
  rendered as a tool-call UI). The plan, task, tool and token panels are built in-house in the style of AI
  Elements and shadcn. The fonts (IBM Plex Sans and Mono, Fraunces) are bundled, so the app works offline.
- API: `GET /api/setups`, `/api/history`, `/api/runs/<job>`, `/api/runs/<job>/stream`, `/api/connections?setup=`,
  `/api/live`, `/api/benchmarks`, `/api/benchmarks/<prefix>`; `POST /api/chat {text, setup}`, `/api/rerun {source_job, setup}`,
  `/api/runs/<job>/stop` (only for `chat-*` and `app-*` runs). Interactive docs are at `/api/docs`.

## Known limits

- The trace logs a model call only when the call ends. While a call is running, the panel shows the phase as open
  with its timer; tokens appear when the call returns.
- The harness and Docker must run on the same machine as the server, because chat and rerun go through Harbor
  locally. Building the ad-hoc task image takes a minute the first time.
- "Files the builder reported" lists the paths in the builder's published outputs, not a full diff.
