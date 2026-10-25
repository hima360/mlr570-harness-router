# Practices taken from professional harnesses (2026-10-25)

Research notes: `docs/research/droid-practices.md` and `docs/research/claude-code-practices.md` (written by
Droid GLM-5.3 workers from docs, changelogs and blog posts; quotes not re-checked). oh-my-pi was read directly
(`docs/non-compaction-retry-policy.md`, `ttsr-injection-lifecycle.md`, `advisor-watchdog.md` in can1357/oh-my-pi);
Factory's policy check blocked the Droid worker on that repo twice.

## Adopted (commit after 92404d6)

| Practice | Source | Where |
| --- | --- | --- |
| Output guard: cut replies at invented turns (`### USER`, fake `<returncode>`), keep only the first command block; logged as `guarded` on the call | oh-my-pi stream rules (TTSR); Claude Code retries malformed calls | `cli_models.guard_reply` |
| Restate the step and the one-command rule at the newest end of every call | Droid (recency reminder) | `cli_models.remind`, `worker.run_worker` |
| Show command runtime above 15 s; timeout message says to background long jobs | Droid (runtimes above a threshold, short timeouts) | `worker.HarborEnvAdapter` |
| Starting directory listing given to workers as already-run output | Droid (environment bootstrap) | `orchestrator.env_block` |
| Every plan step must have a check (no trust-the-worker path for planned steps) | Droid docs: exit 0 means finished, not succeeded | `planner.parse_plan` |
| Provider failures (unavailable, 2 timeouts, idle) skip the error handler and are not router errors | Claude Code (provider fallback kept apart from escalation) | `orchestrator.INFRA_EXITS` |
| Output-token cap for `claude/` workers (`CLAUDE_CODE_MAX_OUTPUT_TOKENS`, default 8000) | Claude Code per-model output caps | `claude_code.complete`, `config.max_output_tokens` |
| Never end a step silently: try another approach or publish a blocker | Claude Code (keep working after denials) | `worker.INSTANCE_TEMPLATE` |

Already in place before this research and confirmed by it: fresh-context workers with one return value
(Droid subagents), compaction with pinned first prompt (Claude Code), fixed-menu error handler with no shell
(oh-my-pi advisor has read-only tools and only injects advice), retry-once and fail-fast on provider errors.

## Not adopted yet (worth doing)

- Resume a worker that hit its action limit from its saved transcript instead of a fresh prompt + digest
  (Claude Code `maxTurns` partial results). Medium effort: preload messages into mini-swe-agent.
- Cross-family validator that re-derives a fact independently (Droid: validator from another model family).
- Continue a reply cut at the output limit (Claude Code auto-continue). CLI replies do not expose the stop reason.
- Droid session continuation (`droid exec -s`) instead of re-sending the conversation; changes how calls are billed.
- Retry with jitter and a single per-call budget (Claude Code, oh-my-pi defaults 10 retries, 500 ms base).

## Deliberately not copied

Interactive permission tiers (the task container is the sandbox), waiting out usage limits for hours
(corrupts wall time and cost), description-based subagent delegation (would be a second router), mid-step
model switching, mission-scale validation (Factory: 14x credits for parity), live advisor on every turn
(oh-my-pi; doubles model usage).
