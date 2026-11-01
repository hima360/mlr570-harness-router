# MLR570: harness-aware, cost-efficient model routing

Course project for MLR570 (Advanced Machine Learning). Two phases:

1. **Harness study (data).** Run 3 models (Claude Sonnet 5.5, GPT-6.1 Sol, GLM-5.3 Flash)
   through 4 coding-agent harnesses (Claude Code, Codex CLI, Hermes Agent, ZCode) on
   Terminal-Bench 4.0 and a SWE-Bench Pro sample. Record pass/fail, tokens, cost and time.
   The per-task results become the router's training labels.
2. **Routed multi-agent harness (method).** A planner splits each task into subtasks. Laya
   routes each one to the cheapest capable model, falling back to Arch-Router when unsure, and a
   verifier escalates failures one tier. It is compared against always-Opus and other routers.

Each person owns one model's subscription and runs that model through all four harnesses
with the same runner, task list and settings. The only per-person difference is the config file.

## Layout

| Path | What it is |
| --- | --- |
| `plan.tsv` | The task list everyone runs (`benchmark`, `task_id`, `run_index`). Generated once by `make_plan.py`, seed 570. |
| `tasks/` | Full task-id lists the plan is drawn from. |
| `make_plan.py` | Draws `plan.tsv` from `tasks/`. Run once. |
| `run_grid.py` | The runner: all harnesses, around the clock, waits out usage caps, resumes after restarts. |
| `configs/example.json` | Template. Copy to `configs/<your-name>.json`. |
| `results/<your-name>.jsonl` | Your run log, one JSON line per run. |
| `versions.md` | Pinned versions of every harness, Harbor and opencodex. |
| `RULES.md` | How we keep the three columns comparable. Read it first. |

## Setup

1. Install Docker, [Harbor](https://github.com/harbor-framework/terminal-bench) and opencodex,
   using the versions in `versions.md`.
2. Log in to your subscription (through opencodex for the non-native harnesses). Keep tokens in a
   local `.env` that is never committed.
3. `cp configs/example.json configs/<your-name>.json`, then set `person` and `model`.
4. **Smoke test:** make each harness command in your config solve one task by hand. Fix
   `parse_result()` in `run_grid.py` so it reads pass/fail and token counts from that harness's
   real output, and open a PR so everyone gets the fix.
5. Run it in a terminal that stays open, with the laptop plugged in:

```bash
caffeinate -i python3 run_grid.py configs/<your-name>.json
```

Ctrl+C stops it at any time; restarting skips runs already in your log.

6. Every night:

```bash
git pull && git add results/<your-name>.jsonl && git commit -m "results <your-name> <date>" && git push
```

## Log fields

`ts, person, benchmark, task, run, harness, model, passed, exit_code, duration_s, input_tokens,
output_tokens, cached_tokens, tool_calls, cost_usd, runner_commit, outdir`

`cost_usd` = tokens x the public list price in the config, not what the subscription charges.

## Using an AI assistant

Paste this README and `RULES.md` into Claude or Codex and ask it to do the smoke test for your
harnesses. Tell it explicitly not to edit `plan.tsv`, the prices or the log fields.

## Phase 2 harness (`phase2/`)

Design: `docs/PHASE2_PLAN.md`. Setup and checks:

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv -r requirements-phase2.txt
.venv/bin/python -m pytest tests -q
```

Harbor smoke test with a scripted model server (no subscription needed; needs Docker with `docker compose`):

```bash
.venv/bin/python tests/harbor_smoke/stub_llm_server.py 18999 &
PYTHONPATH=$PWD .venv/bin/harbor run -p tests/harbor_smoke/hello-file -a phase2.agent:RoutedHarness \
  --ak base_url=http://127.0.0.1:18999/v1 --ak policy=fixed:0 -n 1 -o jobs --job-name smoke
.venv/bin/python -m phase2.show_trace jobs/smoke/*/agent
```

Policies (`--ak policy=...`): `cascade` (Laya, Arch-Router below the confidence threshold), `laya`, `arch`,
`random`, `fixed:<tier>` (`fixed:3` = always Opus, `fixed:0` = always cheapest).

### Easy mode

| To | Do |
|---|---|
| Watch runs, compare results, start a run | double-click `Harness.command` (keys: `n` new run, `c` compare, `/` filter, `?` help) |
| Show the TUI on a recorded run (no Docker, no credits) | double-click `Demo.command` |
| See how far an evaluation is | `.venv/bin/python scripts/progress.py [prefix] [-w]` |
| Get a results page to open in a browser | `.venv/bin/python scripts/report_html.py <prefix> [<prefix> ...]` (writes `reports/`) |
| Run an evaluation unattended | `scripts/supervise.sh --config ... --prefix <p> --setups a,b --repeats 2 <tasks>` |
| Draw new held-out tasks | `.venv/bin/python scripts/draw_heldout.py --seed <n>` |

### TUI (start, watch, browse and stop runs)

```bash
.venv/bin/python -m phase2.tui
```

- `c` **Compare results**: pick an evaluation; one line per setup (solved, tests passed, tokens, cost, cost per
  solve, minutes per run, best setup in green), each task's runs per setup, and how far it is (refreshes).
- `n` **New run** has an *Evaluation setup* picker (`opus`, `glm`, `glmreview26`, ...) that fills in the config,
  policy and limits that setup uses in `scripts/run_compare.py` (`phase2/setups.py`).
- The filter box above the jobs list keeps runs whose name or status has every word typed (`ho4 running`).

- Sidebar: `+ New run` and every job in `jobs/` (● running, ✓ passed, ✗ failed, ! error, ■ stopped).
- Main pane: the roles box shows who actually did each job in that run (planner model, router that decided,
  worker models with calls, time, tokens and credits or list-price cost, verifier results, tier table).
  Tabs: **Who did what** (one line per planner/router/worker/verifier action), **Steps**, **Worker turns**
  (the model's text and each command's output), **Harbor log**.
- Keys: `n` new run (task, backend config, policy, caps: max steps, turns per step, idle limit; shows the exact
  `harbor run` command),
  `s` stop the selected running job (kills its process group, removes its container and network),
  `r` refresh, `q` quit. Runs start under `caffeinate -i` and keep going if the TUI is closed.
- Everything comes from the run's own `trace.jsonl`, worker trajectories and Harbor `result.json`.
  Keep the lid open: a sleeping Mac pauses the run and drops the model connections.
- Limits: each model call 180 s (retried once); a step attempt ends after `step_idle_sec` (600) without a model
  reply, or at the 1 h cap; a failed attempt escalates one tier and hands its notes to the next model.
- `droid/` models use the Factory app's bundled `droid` (`/Applications/Factory.app/Contents/Resources/bin/droid`)
  unless `PHASE2_DROID_BIN` is set; an older `droid` on PATH can reject current model ids.
