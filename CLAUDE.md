# MLR570 harness router

Course project: does the agent harness change cost and pass rate, and can a small local
router cut cost by sending each step of a task to the cheapest capable model?
Read `README.md` (setup, layout), `RULES.md` (benchmark rules) and
`docs/PHASE2_PLAN.md` (Phase 2 design and milestones) before changing anything.

## Two parts of this repo
- **Phase 1 (benchmark grid):** `run_grid.py`, `make_plan.py`, `plan.tsv`, `configs/`, `results/`.
  Shared by all three teammates. Never edit `plan.tsv`, prices or log fields (see `RULES.md`).
- **Phase 2 (routed harness):** `phase2/` package, a Harbor external agent
  (`phase2.agent:RoutedHarness`). Planner (Opus 5.5) → DAG of steps → Laya scores each step
  (Arch-Router-1.5B when Laya's confidence is low) → tier table picks the model →
  mini-swe-agent does the step in the task container → verifier runs the step's check and
  escalates one tier on failure.

## Facts to rely on
- Harbor: custom agent = `harbor.agents.base.BaseAgent` with `name()`, `version()`,
  `async setup(environment)`, `async run(instruction, environment, context)`. Commands go
  through `environment.exec(command, cwd=, env=, timeout_sec=)`. Report usage on
  `AgentContext` (`n_input_tokens` includes cache, `n_cache_tokens`, `n_output_tokens`,
  `cost_usd`, `model_usage`, `metadata`). Run with
  `harbor run -d <dataset> -a phase2.agent:RoutedHarness -m <model> -i <task glob> -n 1 -o jobs`.
  Trial result: `<job>/<trial>/result.json`, pass = `verifier_result.rewards["reward"] == 1`.
- Laya: `laya_mlx.load()` then `agent.predict({"task": text}, questions)`; use the `score`
  question type and its expected `score` plus `confidence` (never the argmax: it collapses to
  the middle option). Ask about the task, never offer model names as options.
  Reference: `~/Developer/bonsai-scratch/router-bench/run_laya.py`.
- Arch-Router: likelihood over route names, see `~/Developer/bonsai-scratch/router-bench/run_hf.py`.
- Models are reached through opencodex at `http://127.0.0.1:10100/v1` (OpenAI chat and
  Anthropic messages routes; model ids `provider/model`, confirm with `ocx models list`).
- Cost is always tokens x the list price in config, never the subscription bill.

## Working rules
- Python 3.12. Tests in `tests/` use a fake environment and fake LLM: no network, no Docker.
  Run `pytest tests/` before every commit.
- Keep the Phase 2 baselines (always-Opus, always-cheapest, random) inside the same harness,
  so only the routing policy differs between conditions.
- No secrets in the repo: tokens and keys live in a local `.env`.
- Code changes go on a branch with a PR.
