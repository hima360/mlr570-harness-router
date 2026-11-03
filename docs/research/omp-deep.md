# oh-my-pi (omp) and pi-mono: practices for the phase2 harness (2026-10-25)

Evidence: Source inspected 2026-10-25. Paths are relative to `omp/src/oh-my-pi/` unless they start with `pi-mono/`; `cas/` = `packages/coding-agent/src/`. Numbers from our own trace (`jobs/shadow-relay-ladder-1025c`, 101 calls) are Verified locally. Value / effort marked H/M/L.

## A. Agent loop and stop conditions

**A1. Never execute a reply cut at the output limit; re-ask with a "smaller step" notice.** H / L
- pi-mono `packages/agent/src/agent-loop.ts:273-279`: on `stopReason === "length"`, "Fail them all instead of executing potentially borked calls".
- `cas/prompts/system/length-stop-retry.md:3`: "Reason only about the next step, then call the tool."
- Change, `cli_models.make_text_model._query`: if `completion_tokens >= 0.95 * cfg.max_output_tokens`, drop the reply and call once more with that notice added (one retry, then format error).
- Change, `cli_models.run_cli`: the timeout retry re-sends the *identical* prompt. Let `complete()` pass a `call(attempt)` so the retry adds the same notice. 1025b s2 had four 180 s timeouts in a row (Historical, COORDINATION_DESIGN R7).

**A2. Soft step budget with a forced final report.** H / L
- `cas/task/executor.ts:167-173`: a notice at the budget, a forced report at 1.5x, then 5 grace requests (`:205`).
- Notice text, `:210`: "Wrap up now: finish the current step and deliver your final report."
- Today mini-swe-agent raises `LimitsExceeded` with an empty submission (`.venv/.../agents/default.py:133`).
- Change, `worker.run_worker`: subclass `DefaultAgent.query`.
  - At `step_limit - 5`, append "[budget notice] publish what you found to outputs.txt and submit".
  - Allow 2 grace calls past the limit.
- Change, `orchestrator._attempt` (line 169): also run `_repair_outputs` when `exit_status == "LimitsExceeded"`. Partial facts then reach the error handler and the next tier.

**A3. Format-error cap.** Already in place: mini-swe-agent ends after 3 format errors in a row (`default.py:32`, `RepeatedFormatError`). That matches omp's caps of 3 (`cas/session/turn-recovery.ts:93-101`). No change.

**A4. Loop guard** (5 identical calls, `cas/session/settings.ts:324-326`): skip; 1025c had 0 back-to-back identical commands in 100 worker calls. L / L

## B. Retry, backoff, model fallback

- Classification (`docs/non-compaction-retry-policy.md:40-65`): retry only `stopReason=error` that is not context overflow and only with no "replay-unsafe" output (`:52`).
- Backoff: `min(base*2^(n-1), 8000ms) * (75-100% jitter)` (`:86`); defaults 10 retries, 500 ms (`cas/session/settings.ts:684,700`).
- Fail fast when the provider asks to wait longer than 5 min (`:705`, doc `:90`).
- Hard errors do not back off on the same model: a configured fallback chain switches models with a fresh retry budget (doc `:71,89`), and the primary comes back after a cooldown (`settings.ts:850`).

**B1. Same-tier sibling instead of escalation for provider failures.** H / M
- Today `orchestrator._run_step` turns `INFRA_EXITS` into tier+1. A Droid outage then shows up as an up-tier escalation, which skews the routing comparison and the cost.
- Change: config `fallback: {"droid/x": "claude/y"}` with similar price.
- On an infra exit, retry the same tier on the sibling. Keep `cooldown_until[provider]` on the `Orchestrator`, so later steps routed to that provider go straight to the sibling. Trace event `fallback`, not `escalate`.

**B2.** Jitter `cli_models.RETRY_WAIT_SEC` by 75-100%. L / L

## C. Compaction and handoff

**omp:**
- Keeps the newest 20k tokens (`docs/compaction.md:517`; pi-mono `core/compaction/compaction.ts:128-129` compacts at `window - 16384`).
- "Hard rule: never cut at `toolResult`" (`compaction.md:274`).
- Tool results sent to the summarizer are cut to 2,000 chars (`packages/agent/src/compaction/utils.ts:209`).
- The summary is wrapped as "MUST build on prior work; NEVER duplicate prior work" (`prompts/compaction-summary-context.md:2`).
- A compaction must reach 80% of the trigger or automatic continuation stops instead of looping (`compaction.md:222`).

**C1. Fold in steps (hysteresis) so the prompt prefix stays stable.** H / L
- omp only prunes when the changed suffix is small (≤8k tokens, `agent/compaction/pruning.ts:184`; `compaction.md:249`). The point is to keep the prompt cache.
- Our `worker.compact_messages` re-folds on every call once over budget. `head[1]` (first prompt + PROGRESS) then changes every call, so any prefix cache breaks each turn (inference).
- The trace cannot show this: 1025c `llm_call` events have no cached-token field (Verified locally).
- Change, `compact_messages`: when over budget, fold down to 60% of the budget and keep that boundary until the budget is hit again (state on the CLI model wrapper).
- Change, `llm.record_call`: log `cached_tokens`.

**C2.** In `compact_messages`, replace the output of a command later re-run verbatim with a superseded notice (`pruning.ts:118`). L / L

**C3. Pinned notes file.** M / M
- omp's experimental `context_notes`: a 16 KiB notebook, and only its latest revision is injected (`compaction.md:166-171`).
- This supports design R5 (`notes.md` pinned). Not built yet: `compact_messages` pins only messages 0-1.

**C4. Handoff format.** L / L
- omp has the failing model write "a handoff document for another instance of yourself" (`prompts/handoff-document.md:2`). It is written in the imperative, and writing the handoff never counts as progress (`:13-14`).
- It runs on the live cache with `toolChoice: none` (`docs/handoff-generation-pipeline.md:65-67`).
- Keep our free mechanical digest (`orchestrator._handoff`); put the fixer's note first as an imperative "Next:".

## D. Stream rules, mid-turn interrupts, reasoning length

**How TTSR works:**
- Regex rules run over streamed text and tool arguments.
- On a match, `agent.abort()` is called immediately (`docs/ttsr-injection-lifecycle.md:134`) and the partial output is discarded (`:146`; `contextMode` defaults to `discard`, `:83`).
- `<system-interrupt reason="rule_violation">` is injected (`:154`), and the turn is retried after 50 ms. Each rule fires once by default (`:85`, `:199-201`).

**D1. Real mid-stream cut.** H / M
- `guard_reply` runs after the whole reply has been generated, so it saves no tokens or time.
- Change, `claude_code.complete`: use `--output-format stream-json --verbose` (Unverified on our CLI version).
- Kill the process group once a closed ```` ```mswea_bash_command ```` block or `### USER` has streamed.
- When killed, the usage numbers are lost: estimate tokens from chars and flag the call `estimated_usage` (RULES: cost is tokens x list price).
- Saves only the text tail (reasoning comes first); combine with D2.

**D2. Lower reasoning effort for worker turns.** H / L
- omp's thinking budgets are low 2048, medium 8192, high 16384 tokens (`cas/session/settings.ts:1467-1471`).
- 1025c (Verified locally):
  - Sonnet workers: median 4,109 output tokens per call (max 6,793), at 45-58 s per call.
  - Haiku workers: median 1,648 (max 10,634).
  - Replies are one short command, so most of the output is reasoning (inference).
- Change, `configs/*.json` `reasoning_effort`: `low` for `claude/` worker tiers; keep the planner and fixer higher. Compare timeouts and output tokens across the same pinned plan (R9).

## E. Advisor / watchdog

- Off by default (`docs/advisor-watchdog.md:26`).
- Default tools are `read`, `grep`, `glob`, but `WATCHDOG.yml` can grant `bash` and `edit` (`:5`, `:145`).
- Cadence: `turn` (the default) or `agent-end` = once per run (`:77`), or every Nth update (`:78`).
- At most 4 non-blocker notes per update (`:79`), and a 3-turn cooldown between interruptions (`:181`).
- Prompt (`cas/prompts/advisor/system.md`):
  - "Challenge premature "done", thin verification" (`:7`)
  - "Silence preferred when agent on track." (`:21`)
  - cite only transcript evidence (`:50`)
- No cost multiple is stated anywhere.

**E1. Agent-end reviewer for published facts** (the "wrong values pass weak checks" problem). M-H / M
- Change, `orchestrator._attempt`: after `read_outputs` and before `verify`, for steps with fact outputs, make one call to a cheap model from another family.
- Input: goal, outputs, and `attempt_digest(messages, 3000)`.
- Ask: "For each value, quote the command output that establishes it, or say UNSUPPORTED."
- UNSUPPORTED fails the verdict and takes the existing escalate path. Log it as `review_reject`.
- Cost: about 1 call per step.

## F. Subagents (task tool)

- A subagent gets a role, the shared context and its assignment.
- Optionally it also gets the full plan inline: "NEVER re-read it from the path"; when the plan and the assignment conflict, the assignment wins (`cas/prompts/system/subagent-system-prompt.md:9-15`).
- It returns its final message verbatim by default (`docs/task-agent-discovery.md:287`): "A reply without a tool call ends the run" (`subagent-system-prompt.md:89`).
- Subagents do not verify; main verifies once, because parallel runs "trip on siblings' half-finished edits" (`cas/prompts/system/system-prompt.md:207-209`).
- Gates (`system-prompt.md:175,179`): "Agents lack conversation: supply full slice requirements"; "Shared prerequisite inline; sequence ONLY true dependencies."

**F1. `planner.PLANNER_PROMPT`:** a shared prerequisite (an install, decoding a common file) goes inside the first step that needs it, not into its own step. This targets the long serial chains. M / L

**F2.** `worker.INSTANCE_TEMPLATE`: "Do not run the whole task's test suite; your step check is run for you." L / L

**F3. Prewalk as an optional research arm.** M / M
- The strong model explores, then hands off to the cheap model at its first edit or write (`docs/prewalk.md:3`).
- Possible `policy=prewalk` in `router` / `worker`. It goes against the "no mid-step switching" decision, so treat it as a separate arm, not a default.

## G. Bash tool runtime

- Default timeout 300 s, clamped to 1-3600 s (`docs/bash-tool-runtime.md:30,89`).
- Foreground commands move to the background after 60 s (`cas/exec/settings.ts:266-269`; doc `:253`).
- Output: 50 KiB inline, as a 20 KiB head plus a rolling tail (`:218-219`).
- Per-line cap of 768 bytes (`cas/tools/settings.ts:84-87`).
- The full output is saved and the model is told "Read artifact://<id> for full output" (`:280`).
- Non-interactive environment: `PAGER=cat`, `GIT_PAGER=cat`, `EDITOR=true`, `GIT_TERMINAL_PROMPT=0`, `TERM=dumb`, `NO_COLOR=1`, `CI=true` (`:174-176`).

**G1. `worker.clip_output`:** cut each line to about 500 chars before the head+tail clip. One minified JSON line can fill the 10k budget and stay in every later call. H / L

**G2. `HarborEnvAdapter.execute`:** pass `env=` with the variables above plus `DEBIAN_FRONTEND=noninteractive` and `PIP_NO_INPUT=1`. Leave out `CI=true`, which changes how some tests behave. Pagers and prompts then stop hanging until the 180 s timeout. M / L

**G3. Save the full output when clipped:** write it to `/tmp/steps/<id>/out/<n>.log` (a second exec, only when clipped) and name that path in the elision note. M / L

## H. Provider quirks
Per-model catalog flags (GLM-5.2 efforts only `["high","max"]`, `docs/provider-quirks.md:468`); our CLIs hide the wire format. Only lesson: check each `reasoning_effort` value per backend/model and fail loudly on unsupported levels. L/L

## I. System prompt rules worth copying

- "NEVER fabricate output; ... unobserved = `[INFERENCE]`" (`system-prompt.md:238`)
- "NEVER settle for plausibility when another call reduces uncertainty" (`:97`)

**I1. `worker.outputs_hint`:** "Publish only values printed by a command you ran in this step; if a value is inferred, say so in your submit message." This supports E1. M / L

## J. Evaluating harnesses (`packages/metaharness`)

- Its Harbor runner resumes jobs: finished trials are reused and errored trials are re-run, selected by exception type (`README.md:69-71`; default `CancelledError`, `:144`).
- Difficulty-calibrated pass-rate projection from sibling arms, a one-parameter Rasch fit (`src/experiments.ts:130-145`).
- Per-turn trace reports from two cheap models, about $0.001 each (`README.md:156-161`).

**J1. Analysis:** re-run trials that ended on infra failures (ProviderUnavailable, CancelledError) instead of scoring them 0. For partial grids, report the calibrated projection next to the raw pass rate. Check `RULES.md` first. M / L

## K. Corrections to `docs/HARNESS_PRACTICES.md`

1. **Output guard row.** TTSR aborts *mid-stream*, discards the partial reply, injects the rule and retries (section D). `guard_reply` cuts a finished reply and keeps it, so it saves no tokens or time. Write "inspired by TTSR", not "TTSR".
2. **"oh-my-pi advisor has read-only tools and only injects advice."** True only of the default tools: `WATCHDOG.yml` can grant `edit`, `write` and `bash`. It is also a live reviewer, not an error handler.
3. **"Live advisor on every turn (oh-my-pi; doubles model usage)."** The advisor is off by default. The `agent-end` cadence reviews once per run. "Doubles" appears nowhere in the source: Unverified, delete it.
4. **Retry row** ("10 retries, 500 ms base"). Correct but incomplete. Add:
   - a cap of 8 s per delay, with 75-100% jitter
   - fail fast when the provider asks for more than 5 min
   - overflow is never retried, it goes to compaction
   - hard errors switch models through a fallback chain with a fresh budget
5. **"Continue a reply cut at the output limit."** omp continues only when the cut turn already delivered text or a tool call below the threshold. A cut during reasoning only is discarded with a reminder to work in smaller steps (`compaction.md:126-128`). pi-mono fails every tool call from a truncated message. For one-command workers, discard plus a notice (A1) fits better than continuing.
