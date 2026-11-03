# Claude Code harness practices → mlr570 phase2

Sources: claude-code CHANGELOG.md (cloned under `scratchpad/harness-research/cc/src/claude-code`; line numbers below), the `ralph-wiggum` plugin, and code.claude.com docs (sub-agents, env-vars, costs, settings). Line refs are CHANGELOG.md unless another file is named.

## Practices worth copying

**1. One shared retry budget per model call, with bounded backoff.**
What: every failure mode of one model call (timeouts, dropped streams, non-streaming fallback) draws from one budget; overload (529) gets exponential backoff; hard give-up after a fixed count.
Evidence: line 1568 "one limit now covers a whole model call", "at most 14 requests"; line 1673 the non-streaming fallback "shares the request's retry budget"; lines 993/702 `CLAUDE_CODE_OVERLOADED_RETRY_BASE_DELAY_MS` / `MAX_DELAY_MS`; line 1347 "gives up after three timeouts".
Apply: `phase2/cli_models.py run_cli` (~line 130) retries once per failure kind today; give the call one attempt budget with backoff, and log budget exhaustion like `ModelTimedOut`. Keeps R7 semantics, adds backoff and a total cap. **High value / low effort.**

**2. Continue from truncated output instead of failing the turn.**
What: a reply cut at the output-token limit is auto-continued (a fresh "continue" request), and a stream that dies mid-response keeps its partial text.
Evidence: line 8276 "automatically continue when response is cut off due to output token limit"; line 1321 "continue from the partial response"; line 4784 partial "kept with an incomplete-response notice".
Apply: `phase2/worker.py _make_model` / `cli_models.py`: detect finish_reason=length or a truncated CLI reply, append the partial as an assistant message plus a user "continue exactly where you stopped", re-call once. Targets your 16k-token replies and 180 s timeouts directly. **High value / medium effort.**

**3. Explicit per-model output-token caps; reserve context for output.**
What: output limits are a configured per-model value, and the effective context window subtracts them, so a max-size reply can never overflow the window.
Evidence: line 7146 "default maximum output token limits... to 64k tokens"; env `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (lines 4572, 7461); line 8100 "effective context window (which reserves space for max output tokens)".
Apply: `phase2/claude_code.py` / `droid.py complete()`: pass a max-output option per tier (small for cheap tiers); `worker.py compact_messages`: subtract expected output size from `context_budget_chars`. **High value / low effort.**

**4. Compaction: threshold-triggered, loss-aware, retried on a fallback model.**
What: compaction fires at a percentage of a window, keeps dense blobs out of the summary, re-sends essential lists afterwards, and retries the summarize call on a fallback model if refused.
Evidence: `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (env-vars doc: "percentage of the auto-compact window at which auto-compaction triggers"); line 8 compaction "keeping too much... dense text, such as base64"; line 270 sends "the skill list again afterwards"; line 1955 compaction "retries on a fallback model".
Apply: `phase2/worker.py compact_messages` (R5) is close; add (a) strip hexdump/base64-like lines from PROGRESS before folding, (b) re-append the outputs_hint/task block after compaction, (c) trigger at ~80% of budget, not only at 100%. **High value / low effort.**

**5. Malformed output: show the parse error, retry twice, then end the turn.**
What: an unparseable tool call is retried up to twice with the error shown to the model before the turn fails.
Evidence: line 143 "retries the call up to twice before ending the turn with an error".
Apply: `phase2/worker.py` (mini-swe-agent format errors): when a text-format reply has no/extra command block, send one message quoting exactly what was wrong (plus TEXT_FORMAT_HINT) and re-call; cap at 2 per turn. Flash/GLM's format breaks currently burn whole turns silently. **High value / low effort.**

**6. Retry loops can never exceed the turn limit.**
What: alternation of failure modes (unparseable call, then truncation) must not create an infinite turn.
Evidence: line 2044 "a turn that could retry indefinitely, ignoring `--max-turns`" was fixed.
Apply: `phase2/worker.py run_worker`: every re-ask from #2/#5 increments the same counter checked against `step_limit`, so the 25-action ceiling stays absolute. **High value / low effort.**

**7. Verify at stop time and feed the failure back as a turn (Stop-hook loop).**
What: a hook runs when the model tries to stop; it can block the stop and inject the check result as the next prompt, looping until a promise holds or iterations run out.
Evidence: `plugins/ralph-wiggum/hooks/stop-hook.sh` returns `{"decision": "block", "reason": $PROMPT}` plus an iteration-count message; line 5264 Stop hooks return "additionalContext to give Claude feedback and keep the turn going"; costs doc: "When Claude can verify its own work, it catches issues".
Apply: `phase2/orchestrator.py _attempt`: generalize `_repair_outputs` — when the worker Submitted but `verify` failed, give the SAME model one continuation turn with the check output before triage/escalation. **High value / low effort.**

**8. Subagent reports: concise, structured, and errors are never swallowed.**
What: subagents return a summary to the parent (not their transcript); API errors surface as errors with type, status, request id, model.
Evidence: sub-agents doc: the subagent "returns only the summary"; line 7512 "more concise subagent final reports"; line 4786 "reporting API errors... as successful results" fixed; line 3564 error to parent "includes the error type, status, request id".
Apply: `phase2/orchestrator.py _handoff` / `worker.py WorkerResult`: prepend a fixed header to the digest — exit_status, model, n_calls, check verdict — and never let LimitsExceeded/ModelTimedOut read as a normal attempt in the blackboard. **High value / low effort.**

**9. Turn-limit hits return partial work that is resumable.**
What: a subagent that hits `maxTurns` returns its output "marked as partial", and can be resumed to continue rather than restarted.
Evidence: sub-agents doc, `maxTurns`: "returns its output marked as partial, and Claude can resume it".
Apply: `phase2/orchestrator.py _attempt`: when `result.exit_status == "LimitsExceeded"`, start the next attempt by replaying `worker_<step>_a<n>.json` messages plus one user turn ("continue from your last command"), instead of a fresh prompt + digest. The 25-action limit currently discards live context. **High value / medium effort.**

**10. Provider unavailability falls back within the tier, separately from capability escalation.**
What: a first-call 404 or provider error triggers the session's fallback model chain; refusals retry "on the previous model of the same tier". Capability escalation is a different mechanism.
Evidence: line 3564 "sub-agents dying on a first-call model 404: they now use the session's fallback model chain"; line 1523 "retries once on the previous model of the same tier"; settings doc: `fallbackModel` is an "ordered chain".
Apply: `phase2/cli_models.py ProviderUnavailable`: retry the call once on the next model in the same tier (or the CLI's other backend) before ending the attempt; trace it as `provider_fallback`, NOT a router error, so the paper's routing-error accounting stays honest. **High value / low effort.**

**11. Timeouts split: headers vs stream-idle, not a flat wall clock.**
What: `API_TIMEOUT_MS` (default 600000) bounds only time to first response headers; a separate 5-minute body-idle watchdog kills a stalled stream; long thinking gets progress events so idle timeouts don't fire.
Evidence: env-vars doc: `API_TIMEOUT_MS` "has to receive its response headers"; `API_FORCE_IDLE_TIMEOUT` "5-minute body idle timeout"; line 3377 "The stream now carries progress events".
Apply: `phase2/claude_code.py` / `droid.py`: where the CLI can stream, kill on idle (no bytes) instead of the flat 180 s; otherwise raise the wall timeout for slow tiers and rely on R7's two-timeout escalation. **High value / high effort** (needs streaming support in the CLI backends).

**12. Keep the prompt prefix byte-stable for the cache.**
What: date strings were moved out of the system prompt and the prompt is rendered once, because any prefix change is a full cache miss.
Evidence: line 7788 "moving date out of system prompt"; line 3048 "record the system prompt and tool definitions once instead of re-rendering".
Apply: opencodex/litellm path (`worker.py _make_model`): keep the message list append-only, hoist time-varying text into the last user message, and enable provider prompt caching — the 40k-token resend is the biggest per-call cost. The stateless CLI path (`cli_models.py serialize`) can't benefit; skip it. **Medium value / medium effort.**

**13. Don't-give-up rule after a denial.**
What: subagents keep working after a tool denial and try alternative approaches instead of stopping.
Evidence: line 8275 "continue working after permission denial, allowing them to try alternative approaches".
Apply: `phase2/worker.py INSTANCE_TEMPLATE`: one line — "if a command fails, try a different approach; never end your step without submitting or publishing a blocker". **Medium value / low effort.**

## What NOT to copy

- **Idle compaction** (line 507: compact after ~54 min idle before the 1-hour cache expires). Tuned for interactive sessions and cache TTLs; our workers are stateless single attempts.
- **Retry watchdog that waits out rate/usage limits** (`CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS`, lines 794, 2055). A harness that sleeps for hours past a 429 corrupts wall-time and cost comparisons; your current fail-fast `ProviderUnavailable` handling is right for a benchmark.
- **Description-based subagent delegation** (sub-agents doc: 15,000-token description budget deciding who runs). Our router is Laya scoring step goals; a second, description-driven router would confound the routing study.
- **The interactive permission stack** (allow/ask/deny rules, auto-mode classifier, PreToolUse approval). It exists because Claude Code runs on a developer's machine; our `writes`-overlap rule plus the task container already bound workers, and the classifier needs Anthropic server infrastructure.
- **Auto-continue without accounting** (#2 uncapped): CC has user oversight; in a headless study, continuation turns must count against the step budget (#6) or cheap models get unlimited output.
