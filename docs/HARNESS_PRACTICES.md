# Practices taken from professional harnesses

Research (2026-10-25), all in `docs/research/`:
- First pass by Droid GLM-5.3 workers: `droid-practices.md`, `claude-code-practices.md`.
- Deep pass by four Opus 5.5 agents that re-checked the first pass against sources and read code:
  `cc-deep.md` (Claude Code changelog, docs, Anthropic engineering posts), `droid-deep.md` (Factory posts and
  docs), `omp-deep.md` (oh-my-pi and pi-mono source), `codex-terminus-deep.md` (OpenAI Codex CLI, Terminus-2,
  mini-swe-agent). The deep pass confirmed all 25 first-pass claims, with these corrections: Droid session
  continuation (`droid exec -s`) still sends the full history (no saving); Claude Code's `claude -p` JSON does
  report `stop_reason`; omp's advisor is off by default and can be given edit/bash tools; omp discards (does not
  continue) replies cut at the output limit.

Verified by me before building: Harbor discards all output when its exec timeout fires
(`harbor/environments/docker/docker.py:780`); mini-swe-agent retries a failed model call 10 times with 4-60 s
waits (`minisweagent/models/utils/retry.py:21`) and stops after 3 format errors in a row
(`agents/default.py:32`); `claude -p --output-format json` returns `stop_reason` (live call, 2026-10-25).

## Adopted

| Practice | Source | Where |
| --- | --- | --- |
| Submit gate: on submit, run the step's check and look for its promised outputs; on failure the worker gets the check's output (not the command) and keeps its context; at most `submit_checks` (2) per attempt | Terminus-2 double confirmation, Claude Code Stop hooks, Droid validators (all three deep agents ranked it first) | `worker.SubmitGate`, `HarborEnvAdapter.gate` |
| Commands run under coreutils `timeout` inside the container, so a slow command returns 124 with partial output instead of nothing; `# timeout: N` first line asks for up to 900 s | Codex / Terminus return partial output; Droid lets the model set the timeout | `worker.bounded`, `HarborEnvAdapter.execute` |
| Non-interactive shell env (PAGER=cat, EDITOR=true, GIT_TERMINAL_PROMPT=0, TERM=dumb, TQDM_DISABLE=1, ...) | mini-swe-agent text config, oh-my-pi bash tool | `worker.NONINTERACTIVE_ENV` |
| Long lines cut to 1,000 chars (both ends kept) before the whole-output clip | oh-my-pi (768 bytes per line) | `worker.clip_output` |
| A retry after a reply timeout tells the model its reply was discarded and to answer shorter | Terminus ("NONE of the actions... were performed") | `cli_models.run_cli(retry_call=)`, `droid.py`, `claude_code.py` |
| A reply cut at the output limit runs nothing | pi-mono, oh-my-pi | `claude_code.complete` (`stop_reason`), `CliTextModel._query` |
| Soft budget: from 5 actions left, the reminder says to publish findings and a blocker; missing outputs are repaired after an action-limit stop too | oh-my-pi task executor | `CliTextModel._query`, `orchestrator._attempt` |
| mini-swe-agent model retries capped at 3 | Codex never retries permanent errors | `worker.py` (`MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT`) |
| Output guard: cut replies at invented turns, keep only the first command block (after the full reply, so it saves no tokens; inspired by, not equal to, omp's streaming rules) | oh-my-pi TTSR, Claude Code malformed-call retries | `cli_models.guard_reply` |
| Recency reminder of the step and the one-command rule on every call | Droid | `cli_models.remind` |
| Command runtime shown above 15 s; workers get the starting listing as already-run output | Droid | `worker.HarborEnvAdapter`, `orchestrator.env_block` |
| Every planned step needs a check (the planner's single-step fallback still has none) | Droid docs | `planner.parse_plan` |
| Provider failures skip the error handler and are not router errors | Claude Code | `orchestrator.INFRA_EXITS` |
| Output cap for `claude/` workers (8,000 tokens) | Claude Code | `config.max_output_tokens` |
| Variant config with Claude workers at low reasoning effort (Sonnet workers wrote a median 4.1k output tokens per call in 1025c) | oh-my-pi effort budgets | `configs/phase2-ladder-fast.json` |

## Not adopted yet, ranked

1. **Prompt caching is not happening** (1025c: 0 cached of 1,927,222 input tokens; input was 59% of its $2.26).
   The CLIs get the conversation as one growing message, so no prefix is reused. Fix: send turns as separate
   messages (`claude -p --input-format stream-json`), keep compaction folds stable between calls (Factory and omp
   compact once to a low mark and reuse it). Needs a live test of the CLI flags.
2. **Value reviewer**: one cheap call from another model family between `read_outputs` and `verify` that must
   quote the command output behind each published fact or answer UNSUPPORTED (omp advisor "challenge premature
   done"; Droid validators from a different family). Targets the wrong-seed / wrong-key failures.
3. **Same-tier provider fallback** with a per-run cooldown instead of an up-tier escalation (omp, Claude Code).
4. Model-written handoff summary (Codex compaction prompt) when a failed attempt left no notes.
5. Step limits by cost, not action count (Terminus: unlimited turns; make `_calculate_cost` real so
   mini-swe-agent's `cost_limit` works).
6. Cut a reply mid-stream after the first command block (true TTSR); needs streaming from the CLIs.

## For the paper (from Factory and omp metaharness)

Report routed pass rate as a percentage of always-Opus pass rate, and cost per successful run; reprice each
routed trace at top-tier prices for the counterfactual. Re-run trials that died on infrastructure errors instead
of scoring them 0.

## Deliberately not copied

Interactive permission tiers (the container is the sandbox); waiting out usage limits for hours (corrupts wall
time and cost); description-based subagent delegation (a second router); mid-step model switching; mission-scale
validation (Factory: 14x credits for parity); a todo tool (Claude Code dropped it for newer models); a
repeated-command guard (1025c had no back-to-back identical commands in 100 worker calls).
