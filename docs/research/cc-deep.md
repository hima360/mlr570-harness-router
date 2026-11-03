# Claude Code as a harness: verification + missed practices (2026-10-25)

CHANGELOG = `cc/src/claude-code/CHANGELOG.md`. Docs and posts fetched 2026-10-25. No model runs; evidence is `Source inspected`.

## A. Verification of docs/research/claude-code-practices.md

| # | Verdict | Evidence checked |
|---|---|---|
| 1 Shared retry budget | CONFIRMED | L1568 "one limit now covers a whole model call"; L1673 fallback "shares the request's retry budget". Caveat: L1347 ("gives up after three timeouts") applies only to `CLAUDE_CODE_RETRY_WATCHDOG` sessions. |
| 2 Continue truncated output | CONFIRMED | L8276 "automatically continue when response is cut off"; L1321 "continue from the partial response"; L4784 "partial is now kept". |
| 3 Output caps, reserve context | CONFIRMED | L7146 (64k default for Opus 4.6); L8100 "reserves space for max output tokens"; env-vars doc: raising it "reduces the effective context window". |
| 4 Compaction | CONFIRMED | env-vars doc: CLAUDE_AUTOCOMPACT_PCT_OVERRIDE "can't raise the threshold"; L8 dense "base64 or SVG"; L270 "skill list again afterwards"; L1955 "retries on a fallback model". |
| 5 Malformed call: retry twice | CONFIRMED | L143 "retries the call up to twice before ending the turn". |
| 6 Retries bounded by turn limit | CONFIRMED | L2044 "could retry indefinitely, ignoring `--max-turns`". |
| 7 Stop-hook loop | CONFIRMED | `plugins/ralph-wiggum/hooks/stop-hook.sh:171` `"decision": "block"`; L5264 additionalContext "keep the turn going"; costs doc "When Claude can verify its own work". |
| 8 Concise reports, errors surfaced | CONFIRMED | sub-agents doc "returns only the summary"; L7512; L4786; L3564 "error type, status, request id, and model". |
| 9 maxTurns partial, resumable | CONFIRMED | sub-agents doc "returns its output marked as partial". |
| 10 Fallback within tier | CONFIRMED | L3564; L1523 "previous model of the same tier"; settings doc: fallbackModel "is an ordered chain". |
| 11 Header vs idle timeouts | CONFIRMED | env-vars doc API_TIMEOUT_MS "receive its response headers", API_FORCE_IDLE_TIMEOUT "5-minute body idle timeout". Caveat: L3377 covers only Bedrock. |
| 12 Byte-stable prefix | CONFIRMED | L7788 "moving date out of system prompt"; L3048 "record the system prompt and tool definitions once". |
| 13 Keep working after denial | CONFIRMED, but the **line number is WRONG** | The quote is on L8278, not L8275. |
| NOT-copy list | CONFIRMED | L507 idle compaction; L794 and L2055 watchdog; sub-agents doc "15,000-token description budget". |

**Correction to docs/HARNESS_PRACTICES.md.** It says that for auto-continue, "CLI replies do not expose the stop reason". For `claude -p --output-format json` that looks wrong. The SDK parses `stop_reason`, `num_turns` and `terminal_reason` from the CLI's `result` object (`claude-agent-sdk-python/src/claude_agent_sdk/_internal/message_parser.py:318`; `types.py:1704,1718`). Source inspected only; not verified live. Droid's JSON was not checked.

## B. Practices the file missed

### B1. Block the submit until the step's check passes, inside the attempt (Stop-hook gate). High value, low effort
- **What it is:** Claude Code's agent loop ends when the model stops calling tools. A Stop hook (or `/goal`) runs a check at that moment. If the check fails, it blocks the stop and feeds the result back into the same context. The cap is 8 consecutive blocks.
- **Evidence:**
  - Best-practices doc: "Claude stops when the work looks done."
  - Best-practices doc: a Stop hook "blocks the turn from ending until it passes."
  - CHANGELOG L5699: "ends with a warning after 8 consecutive blocks".
- **Gap in our harness:** the worker never sees `step.check`. `worker.INSTANCE_TEMPLATE` has no check field, and `verifier.verify` runs only after the attempt ends. Every wrong-but-submitted step therefore costs a full new attempt plus a digest, and the attempt loses its live context.
- **Change:**
  - In `worker.HarborEnvAdapter.execute`, when the SUBMIT sentinel arrives, run `step.check` with `environment.exec` before raising `Submitted`.
  - If the check fails, return the output as the observation: "Submission rejected: check `<cmd>` exited N: <tail>. Fix it, or publish blocker=..., then submit."
  - Cap at 2 rejections per attempt, and count each one against `step_limit` (practice #6).
  - Log a `submit_blocked` trace event. This supersedes #7, which proposes a fresh continuation after the attempt.

### B2. Show the check to the worker as its verification target. High value, low effort
- **Evidence:** best-practices doc: "Give Claude a check it can run" and "Have Claude show evidence rather than asserting success."
- **Change:** add `Your step is accepted only if this command exits 0: {{check}}. Run it yourself before submitting.` to `worker.INSTANCE_TEMPLATE`, and pass `check=step.check` in `run_worker`.
- **Risk:** a worker could game a format-only check. B4 handles that.

### B3. Read `stop_reason` / `num_turns` from `claude -p` results. High value, low effort
- **Evidence:** `message_parser.py:318` (`stop_reason=data.get("stop_reason")`). CHANGELOG L8276 says the CLI may already auto-continue internally.
- **Change:**
  - `claude_code.complete`: return `result.get("stop_reason")` and `num_turns` in usage.
  - `cli_models.make_text_model._query`: on `max_tokens`, do one "continue exactly where you stopped" re-call that counts against `step_limit`.
  - Alert when `num_turns > 1` (hidden internal turns inflating cost).
- **Effect:** practice #2 becomes doable for `claude/` workers.

### B4. Lint checks before running: a "sprint contract" for what done means. High value, low effort
- **Evidence:**
  - Harness-design post (anthropic.com/engineering/harness-design-long-running-apps): generator and evaluator agree on scope and verification before each sprint.
  - `/goal` doc: a good condition has "One measurable end state" and "A stated check", plus constraints.
- **Change:** in `planner.parse_plan` or `plan_shape`, flag a check as *format-only* when it only greps `/tmp/steps/<id>/outputs.txt` with a regex. Also flag checks that test only existence (`test -f`, `grep -q name=`).
  - For fact outputs that a later step depends on, send one revise request to the planner.
  - Log `weak_checks` in the trace.
- **Targets:** the known problem "wrong values pass weak checks".

### B5. Independent, blind re-derivation of high-fan-out facts. High value, medium effort
- **Evidence:**
  - Best-practices doc: "A fresh context improves code review since Claude won't be biased toward code it just wrote."
  - Harness-design post: "tuning a standalone evaluator to be skeptical" is "far more tractable".
- **Change:** a new `orchestrator._validate_fact(step, name)`, used only for fact outputs consumed by 2 or more later steps (or by the final step).
  - It runs a short worker on the cheapest tier from a different model family, with `REPAIR_STEP_LIMIT` turns.
  - The validator gets the goal and the source data but not the published value.
  - On a mismatch, mark the step failed with the check output "validator derived X, step published Y". The existing triage and rerun path takes it from there.
- **Value:** this is the targeted version of HARNESS_PRACTICES' "cross-family validator".

### B6. Planner effort-scaling rules in the prompt. High value, low effort
- **Evidence:** multi-agent research post (anthropic.com/engineering/multi-agent-research-system):
  - simple tasks get one agent with "3-10 tool calls"; comparisons get 2-4 subagents; only complex research gets "more than 10 subagents".
  - an early failure was "spawning 50 subagents for simple queries".
  - each subagent needs "an objective, an output format, guidance on the tools and sources", plus boundaries.
- **Change:** in `planner.py` PLAN_PROMPT, replace the soft line 33 ("prefer fewer, larger steps") with explicit rules:
  - 1 step when the task touches one artifact or the instruction is under N lines;
  - 2-3 steps for a chain;
  - more steps only for independent parallel branches.
  - In `parse_plan`, reject or merge plans whose depth equals the step count and exceeds 3 (`plan_shape` already computes `parallelism`).
  - Optional per-step `hints` (sources, out of scope).
- **Targets:** long serial chains.

### B7. Save large command output to a file instead of discarding the middle. Medium value, low effort
- **Evidence:**
  - CHANGELOG L8181: "large bash command outputs to be saved to disk instead of truncated".
  - CHANGELOG L7615: tool results over 50K characters "persisted to disk".
  - CHANGELOG L6523: truncation prompt gives "format-specific recipes (e.g. `jq` for JSON...)".
- **Change:** in `worker.HarborEnvAdapter.execute`, when the output exceeds `max_output_chars`, write it to `/tmp/steps/<id>/out_<n>.txt` (one extra exec with a quoted heredoc, or wrap the command with `tee` under `set -o pipefail`).
  - `clip_output` names the path and suggests `grep -n`, `sed -n` or `jq` (if JSON).

### B8. Stall detector: the same command and the same output, repeated. Medium value, low effort
- **Evidence:**
  - `/goal` doc: when there is "no tool use for several turns in a row", Claude Code stops the loop.
  - Research post failure mode: "agents continuing when they already had sufficient results".
- **Change:** in `cli_models.make_text_model._query`, or in a `DefaultAgent` subclass in `worker.run_worker`, hash (command, output) per turn.
  - On the third identical pair, inject "You ran this 3 times with the same result: change approach or publish blocker=...".
  - On the fifth, end the attempt with exit status `Stalled`. Count it as a router error, not infra.
- **Effect:** saves part of the 25 to 40-action budget.

### B9. Fresh context beats accumulated corrections; resume only on progress. Medium value, low effort
- **Evidence:**
  - Best-practices doc: "After two failed corrections, `/clear` and write a better initial prompt."
  - Best-practices doc: a clean session "almost always outperforms a long session with accumulated corrections".
- **This refines #9:**
  - After a *failed check*, keep today's design: fresh prompt plus `_handoff` digest.
  - Replay the transcript only for `LimitsExceeded` when the last K turns made progress (new files, new outputs).
- **Change:** a branch in `orchestrator._run_step` on `result.exit_status`.

### B10. Notes survive compaction: re-inject published outputs. Medium value, medium effort
- **Evidence:**
  - Context-engineering post (anthropic.com/engineering/effective-context-engineering-for-ai-agents): agents keep notes "outside of the context window", for example "a NOTES.md file".
  - Context-engineering post: tool-result clearing is among the "safest lightest touch forms of compaction".
- **Change:** in `worker.compact_messages`:
  - Stage 1: replace old observations with `[output cleared]` but keep the thoughts and commands.
  - Fold into PROGRESS only if the context is still over budget.
  - Append the current contents of `/tmp/steps/<id>/outputs.txt` to the PROGRESS block. The adapter would cache the last `cat` result, or read it once per compaction.
  - Published facts then survive the fold.

### B11. Poka-yoke the submit line: no printf escaping. Medium value, low effort
- **Evidence:** building-effective-agents post: keep formats "close to natural text" and avoid "escaping". Its SWE-bench fix was to "always require absolute filepaths".
- **Gap:** `INSTANCE_TEMPLATE` asks for `printf '<SUBMIT>\n<sentences>\n'`. Any `'` or `%` in the summary breaks it. That is the same pitfall `outputs_hint` already avoids with a heredoc.
- **Change:** make the submit line a quoted heredoc, `cat <<'EOF'\nCOMPLETE...\n<summary>\nEOF`. `HarborEnvAdapter.execute` already parses the first line, so nothing else changes.

### B12. Encourage batched read-only inspection in one block. Medium value, low effort
- **Evidence:** research post: parallel tool calls "cut research time by up to 90%".
- **Change:** one line in `TEXT_FORMAT_HINT`: "Combine independent inspections in one command (`ls; head -50 f; grep -n x g`)". Keeps one block; fewer turns.

### B13. Per-step dollar budget. Low value, low effort
SDK `types.py:2411` `max_budget_usd`. Set `DefaultAgent(cost_limit=...)` per tier in `worker.run_worker` (now `0`).

### B14. Ablate one harness component at a time (for the paper). High value, low effort
- **Evidence:** harness-design post: remove components one at a time to find which are load-bearing.
- **Evidence:** research post: "token usage by itself explains 80% of the variance".
- **Change:** a config flag per R-feature in `phase2/config.py`. Add tokens per task as a covariate in the analysis, so a router "win" is not just "fewer tokens means lower pass rate".

### B15. Skip a todo tool
CHANGELOG L3971: todo tools removed for Opus 4.8, Sonnet 5 and newer. `cli_models.remind` covers it. Consider dropping `TodoWrite` from `droid.py:39` (unverified).

Already aligned: outputs.txt as an external store (research post's answer to the "game of telephone"), end-state checks, short returns, actionable timeout and elision messages.

## Change Record
Task: Claude Code harness research, deep pass
Linear: None
Update: 13/13 confirmed (#13 line is 8278). claude -p exposes stop_reason (SDK). Workers never see step.check.
Evidence: Source inspected (CHANGELOG, SDK source, phase2/*.py); docs and blog posts fetched 2026-10-25
Next: Implement B1+B2 in worker.HarborEnvAdapter.execute / INSTANCE_TEMPLATE with a test in tests/ using the fake env
Edited by: Claude Code (Anthropic, Opus 5.5) — 2026-10-25T00:00:00+00:00
