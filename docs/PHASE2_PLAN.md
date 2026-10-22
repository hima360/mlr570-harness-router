# Phase 2: routed multi-agent harness — build plan

## Context
MLR570 Phase 2 needs a harness that splits a coding task into steps, has a small local router pick the cheapest capable model per step, and checks each step. It must run inside Harbor on Terminal-Bench 4.0 / SWE-Bench Pro so it is scored on the same tasks as Phase 1. Build window 23 Oct – 4 Nov, router tuning 5–8 Nov, eval runs 9–11 Nov.

Decisions made: worker for each step = mini-swe-agent (same worker for every model); parallel steps only when their files don't overlap; "fine-tune Laya" = small head trained on frozen Laya embeddings, reported with 6-fold cross-validation.

## Architecture (Harbor external agent, runs on the host)
`harbor run -d terminal-bench/terminal-bench@4.0.0 -a phase2.agent:RoutedHarness -m <planner model>`

1. **Planner** — one Opus 5.5 call → JSON DAG: steps with id, goal, depends_on, writes (file globs), acceptance check (shell command). Validated (acyclic, ids, at most 8 steps); invalid → one repair call → else single-step plan.
2. **Router** — Laya `score` (expected tier + confidence, in-process MLX); confidence below threshold → Arch-Router-1.5B (lazy-loaded). Tier → model table in config: 0 GLM-5.3 Flash, 1 GPT-6.1 Sol, 2 Sonnet 5.5, 3 Opus 5.5 (escalation). Table re-set from Phase 1 Pareto data.
3. **Scheduler** — asyncio; a step starts when its dependencies are done; ready steps run together only if their `writes` don't overlap.
4. **Worker** — mini-swe-agent driving the task container through `environment.exec`, model called through opencodex (`http://127.0.0.1:10100/v1`). Private memory = its own history; reads the blackboard summary at start.
5. **Blackboard** — per step: status, model, outputs summary, files touched. Persisted to the trial's logs dir.
6. **Verifier** — runs the step's acceptance check in the container; fail → escalate one tier, retry once; every escalation logged as a router error.
7. **Accounting** — every LLM call's tokens × list price; filled into Harbor `AgentContext` (`n_input_tokens`, `n_cache_tokens`, `n_output_tokens`, `cost_usd`, `model_usage`, `metadata` = DAG + routing decisions + escalations).

Baselines run in the same harness with the router fixed (always-Opus, always-cheapest, random), so only routing differs. Other routers (RouteLLM, NVIDIA, Arch-Router alone, zero-shot / trained Laya) are replayed offline on Phase 1 logs.

## Files (new `phase2/` package in this repo)
- `phase2/agent.py` — `RoutedHarness(BaseAgent)`: `name`, `version`, `setup`, `run`.
- `phase2/planner.py`, `phase2/router.py`, `phase2/scheduler.py`, `phase2/worker.py`, `phase2/blackboard.py`, `phase2/verifier.py`, `phase2/llm.py` (opencodex client + usage), `phase2/config.py` (tier table, prices, threshold, policy: cascade/fixed/random).
- `phase2/replay.py` — offline router replay on `results/*.jsonl`.
- `phase2/train_head.py` — Laya embeddings → head, 6-fold CV by task, threshold tuning.
- `phase2/show_trace.py` — terminal view of one trial's DAG and routing (demo).
- `tests/` — fake environment + fake LLM, no network: planner validation, scheduler order + overlap rule, cascade threshold, verifier escalation, cost maths.
- `CLAUDE.md` — project context, layout, rules (benchmark rules from `RULES.md` still apply).
- `docs/PHASE2_PLAN.md` — this plan, no names or roles.

Reuse: Laya call + criteria from `~/Developer/bonsai-scratch/router-bench/run_laya.py` (`laya_mlx.load()`, `predict(..., "score")`, `confidence`); Arch-Router likelihood scoring from `router-bench/run_hf.py arch`; Harbor `BaseAgent` / `BaseEnvironment.exec` / `AgentContext`; Harbor's built-in mini-swe-agent.

## Milestones
1. **23–26 Oct** — Docker + Harbor + opencodex running; py3.12 venv with harbor + laya_mlx; `RoutedHarness` in fixed-model, single-step mode solves 1 TB task end to end.
2. **27–29 Oct** — planner, DAG, scheduler, blackboard, verifier (fixed model).
3. **30 Oct – 1 Nov** — Laya + Arch-Router cascade, escalation, trace + `show_trace.py`.
4. **2–4 Nov** — `replay.py` on early Phase 1 logs, `train_head.py` scaffold, dry run on 3 tasks.
5. **5–8 Nov** — train head on frozen labels, tune threshold by CV, freeze config.
6. **9–11 Nov** — eval runs.

## Blockers found
- Docker is not installed on this Mac; Harbor needs it (needed for Phase 1 too, by 24 Oct).
- opencodex is not running (`ocx start`) and its catalog lacks GPT-6.1 Sol and Sonnet 5.5 ids (`ocx sync`, then `ocx models list`).
- Phase 2 access to GPT/GLM from this machine is still parked; for development, map all tiers to Claude models.
- `run_grid.py` harness template uses `-t {task} -o {outdir}`; Harbor wants `-i <task>` with `-d`, `-o` is a jobs dir, and the result is `<trial>/result.json` with `verifier_result.rewards.reward` and `agent_result.n_*_tokens` — fix `parse_result()` and the template during the smoke test.

## Verification
- `pytest tests/` passes with fakes (no network).
- `harbor run ... -a phase2.agent:RoutedHarness -i <one task> -n 1` produces a trial `result.json` with reward and filled token/cost fields, and `show_trace.py` prints the DAG and routing.
- `replay.py` reproduces always-cheapest / always-frontier numbers from Phase 1 logs.
