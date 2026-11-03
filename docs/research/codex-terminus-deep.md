# Codex CLI, Terminus-2 and mini-swe-agent vs our phase2 harness (2026-10-25)

Evidence: Source inspected, 2026-10-25. Nothing was run. Sources:
- Terminus-2: `.venv/lib/python3.12/site-packages/harbor/agents/terminus_2/` (written `T2/` below)
- Terminus-1: terminal-bench clone `d28711d`, `terminal_bench/agents/terminus_1.py`
- Codex: clone `970111f` (2026-10-23), `codex-rs/`
- mini-swe-agent: `.venv/.../minisweagent/` (written `mswea/`)

Value / effort is my own estimate.

## A. Practices worth adopting, in priority order

### 1. Check before accepting a submit (Terminus double confirmation) — HIGH value, LOW-MED effort
- **What they do:** the first `task_complete` does not end the run. Terminus-2 shows the terminal again and asks the model to confirm. Only a second `task_complete` in a row ends the run.
- **Evidence:** `T2/terminus_2.py:631` "Are you sure you want to mark the task as complete?". The flag is set at `:1536`, and `:1656` returns only `if was_pending_completion`. Terminus-1 stopped at the first claim (`terminus_1.py:209`). Terminus-2 added the confirmation.
- **Our change:** add a per-step wrapper around `HarborEnvAdapter` in `worker.run_worker`. The adapter is shared by parallel steps (`agent.py:55`), so the state must live in the wrapper.
  - When the reply is `SUBMIT`, run `step.check` first.
  - If the check passes, raise `Submitted`.
  - If it fails the first time, return the check output as the observation: "Your step's check failed: …; fix it, or submit again if the check is wrong."
  - On the second submit, raise `Submitted` as today.
- **Why it beats Terminus:** we can show the real check output, not just ask "are you sure". A failure gets fixed in context on the cheap model instead of going to triage and escalation with a 5k digest.
- **Risk:** the worker could game a format-only check.

### 2. A command timeout must keep its partial output — HIGH value, LOW effort
- **What they do:**
  - Terminus never kills a command. On timeout it shows the current screen and says "It is possible that the command is not yet finished executing" (`T2/templates/timeout.txt`). Each wait is capped at 60 s: `duration_sec=min(parsed_cmd.duration, 60)` (`T2/terminus_2.py:1310`).
  - The Terminus prompt says "Never wait longer than 60 seconds; prefer to poll" (`terminus-json-plain.txt:41`).
  - Codex returns after a yield time of at most 30 s with "Process running with session ID" (`core/src/unified_exec/mod.rs:68-72`, `core/src/tools/context.rs:583`). Timeouts use exit code 124 (`core/src/exec.rs:70`).
- **What we do:** Harbor's docker `exec` raises `RuntimeError("Command timed out…")` and throws away stdout (`harbor/environments/docker/docker.py:780-782`). It only stops the local `docker exec` client (`:889-897`). `HarborEnvAdapter.execute` then returns `output=""`. So after 180 s the model sees nothing. The process inside the container may keep running and writing files (Unverified; Docker usually does not kill exec'd processes when the client dies).
- **Our change:** in `worker.HarborEnvAdapter.execute`, run `timeout -k 5 {T-10}s bash -c {shlex.quote(cmd)}` inside the container.
  - The outer `timeout_sec=T` stays as a backstop.
  - A slow command then comes back normally with rc 124 and its partial output, and the in-container process is actually killed.
  - Add the note: "timed out after N s; output above is partial; run long jobs with nohup … & and poll the log".
  - Check that `timeout` exists in the TB images (coreutils or busybox).

### 3. Do not repeat the same request after a reply timeout — HIGH value, LOW effort
- **What they do:** Terminus does not resend the same request after an over-long reply. It answers with "ERROR!! NONE of the actions you just requested were performed" and asks for smaller chunks (`T2/terminus_2.py:1257-1270`). mini-swe-agent's full template has a branch for `finish_reason=length`: "Respond more concisely and provide exactly one action in the required format." (`mswea/config/mini_textbased.yaml:147`).
- **What we do:** `cli_models.run_cli` retries the identical prompt after a 180 s timeout. If the first try ran long because the model wrote a 16k-token reply, the retry will likely run long too, and then `ModelTimedOut` ends the step. This is the measured "four 180 s timeouts" failure in COORDINATION_DESIGN.
- **Our change:** give `claude_code.complete` and `droid.complete` a different input for the retry. Add one line to the newest message, for example "[Your previous reply took >180 s and was discarded; none of it ran. THOUGHT ≤5 sentences, one command.]". `run_cli` already accepts a `call`; pass a second `call_retry`.
- **Related, Unverified estimate:** an 8,000-token output cap at a typical 50-100 tokens/s takes 80-160 s. That is close to `cli_timeout_sec=180`, so consider a 4,000-token cap for worker tiers.

### 4. Fix weak models' format slips instead of rejecting them, and tell them — HIGH value, LOW effort
- **What they do:** the Terminus parser tries fixes in order (close missing braces, pull JSON out of mixed text). When a fix works it continues and warns: "AUTO-CORRECTED: {fix_name} - please fix this in future responses" (`T2/terminus_json_plain_parser.py:44-57, 310-317`). Extra text "will generate warnings but be tolerated" (`terminus-json-plain.txt:46`).
- **What we do:**
  - We use mini-swe-agent's bare default error message: "Please always provide EXACTLY ONE action in triple backticks, found 0 actions." (`mswea/models/litellm_textbased_model.py:10-12`). It shows no example and does not name the fence tag. We never pass the full template (`mini_textbased.yaml:145-171`).
  - The action regex needs a newline before the closing fence (`litellm_textbased_model.py:8`).
  - A format error still uses up a step (`n_calls += 1` before parsing, `mswea/agents/default.py:148`). Three in a row end the attempt (`:32`).
- **Our change:**
  - (a) In `cli_models.guard_reply`, when there is no `mswea_bash_command` block, rewrite the first ```` ```bash ````, ```` ```sh ````, ```` ```shell ```` or bare fence to `mswea_bash_command`. Add the missing newline before ```` ``` ````. Close an unclosed last fence.
  - (b) Pass `format_error_template=` (the mini_textbased text with its example and the `finish_reason` branch) into `CliTextModel(...)` in `make_text_model`.
  - (c) Keep the guard's reason on the model. On the next call, have `remind()` add "[Warning] your last reply had 3 command blocks; only the first ran." Today `guard_reply` drops commands without telling the model.

### 5. Do not retry errors that cannot succeed; back off with jitter — MED value, LOW effort
- **What they do:** Codex never retries `ContextWindowExceeded`, `UsageLimitReached`, `QuotaExceeded` or `InvalidRequest` (`protocol/src/error.rs:418-447` return `None`). It retries stream, timeout and 5xx errors with 200 ms × 2ⁿ and ±10% jitter (`async-utils/src/backoff.rs:7-16`). Defaults are 5 stream retries and 4 request retries (`model-provider-info/src/lib.rs:68-70`).
- **What we do:** when a CLI error comes after model turns ran, `run_cli` raises a plain `RuntimeError`. That is not in `abort_exceptions`, so mini-swe-agent's tenacity loop retries it **10 times with 4-60 s waits** (`mswea/models/utils/retry.py:21-22`). That covers "prompt too long" and usage-limit errors, so up to about 6 minutes are wasted on calls that cannot succeed.
- **Our change:**
  - In `cli_models.run_cli`, match `is_error` result text for context, limit and invalid errors and raise a new `ModelRequestRejected`. Add it to `CliTextModel.abort_exceptions` and to `orchestrator.INFRA_EXITS` only when it is a quota error.
  - Set `os.environ.setdefault("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "3")` in `worker.py`.
  - Replace the fixed `RETRY_WAIT_SEC=5` with jittered exponential backoff.

### 6. Model-written handoff summary on escalation — MED value, MED effort
- **What they do:**
  - Codex compacts with "Create a handoff summary for another LLM that will resume the task." (`prompts/templates/compact/prompt.md:1`). The summary covers progress, constraints, remaining work and critical data.
  - Codex puts the summary_prefix "Use this to build on the work that has already been done" in front (`summary_prefix.md:1`; `core/src/compact.rs:403`).
  - It keeps real user messages, newest first, up to 20k tokens (`compact.rs:63, 710-800`).
  - Terminus uses three calls: a summary, then a fresh model asks "at least five" questions, then the old context answers them (`T2/terminus_2.py:931-1075`).
- **What we do:** `worker.attempt_digest` is extractive. It keeps 400-char snippets of thoughts and output, newest first, up to 5k chars, and loses facts found early.
- **Our change:**
  - In `orchestrator._handoff`, when an attempt ended `LimitsExceeded`, `TimeExceeded` or failed its check after 8 or more calls, make one extra call on the **same cheap model**. Use its compacted transcript, the Codex prompt and a 2k-char cap.
  - Put the prefix in front and append the check output. Fall back to `attempt_digest` if the call fails.
  - Skip Terminus's question round: it costs two more calls.
  - This fits R6 (notes first). Use the summary only when `notes.md` is empty.

### 7. Limit a step by budget, not by action count — MED value, LOW effort
- **What they do:** Terminus-2 defaults to 1,000,000 turns. A set limit triggers the warning "Consider removing this limit for better task completion." (`T2/terminus_2.py:432-440`). mini-swe-agent defaults to `step_limit: 0` with `cost_limit: 3.` (`mini_textbased.yaml:104-105`). Codex has no turn cap; it compacts at 90% of the window (`protocol/src/openai_models.rs:537`).
- **What we do:** a 25-40 action cap with `cost_limit=0`, because `CliTextModel._calculate_cost` returns 0.
- **Our change:**
  - Return the ledger price from `CliTextModel._calculate_cost`, so mini-swe-agent's `cost_limit` works.
  - In `worker.run_worker`, pass `cost_limit=<per-tier step budget>` and raise `step_limit` to about 80 as a backstop. `step_wall_time_sec` is already a safety cap.
  - Add "N actions left" to `remind()` when 3 or fewer remain. This one is our own idea; none of the three sources does it.

### 8. Environment that never waits for input — MED value, LOW effort
- **Evidence:** mini-swe-agent's text config sets `PAGER: cat`, `MANPAGER: cat`, `LESS: -R`, `PIP_PROGRESS_BAR: 'off'`, `TQDM_DISABLE: '1'` (`mini_textbased.yaml:107-113`). We pass no `env`.
- **Our change:** in `HarborEnvAdapter.execute`, call `self.environment.exec(..., env={...})` with those variables plus `GIT_PAGER=cat` and `DEBIAN_FRONTEND=noninteractive` (these two are my additions).
- **Why:** a pager or apt prompt is a sure 180 s timeout, and progress bars fill the 10k output clip.

### 9. Truncation header with totals — LOW value, trivial effort
- **What they do:** Codex adds "Warning: truncated output (original token count: N)\nTotal output lines: L" (`utils/output-truncation/src/lib.rs:32`) and always reports wall time and exit code (`core/src/tools/context.rs:568-590`).
- **Our 10k-char head/tail clip matches** both Codex (`openai_models.rs:1123`, 10,000 bytes) and Terminus (`T2/terminus_2.py:655`, 10,000 bytes), so the size is fine.
- **Our change:** add the total line count to `worker.clip_output`, so the model can choose `sed -n` ranges.

### 10. Planner guidance — LOW value
- Codex says to run plan steps in parallel, one agent per step where possible (`core/templates/agents/orchestrator.md:42`), and to skip plans for the easiest 40% of tasks (`:34`).
- Our planner prompt already covers this. None of the three harnesses offers a fix for long serial chains.

## B. Things we do that these harnesses suggest are mistakes

1. **A timeout throws away all output and may leave the process running.** See A2 (`worker.py:99-103`, `docker.py:780-782`). Both Terminus and Codex return partial output and keep the command under control.
2. **The retry after a timeout is identical.** See A3 (`cli_models.run_cli`). Terminus tells the model that nothing ran and to shrink the request.
3. **A bare format-error message, and silent guard edits.** See A4. Terminus fixes what it can and says so. mini-swe-agent's own text config gives an example and handles `finish_reason=length`.
4. **Final errors are retried 10× by tenacity.** See A5. Codex lists errors it never retries.
5. **A hard action cap that does not tell the worker.** See A7. Terminus treats a turn cap as harmful. mini-swe-agent bounds work by cost.
6. **The wall-clock call timeout (180 s) does not tell a slow reply from a stalled one.** Codex uses a 300 s idle timeout per stream (`model-provider-info/src/lib.rs:68`).
   - Fix: run `claude -p --output-format stream-json` and reset the timer on each event in `cli_models.run_group`.
   - MED value, MED effort. Lower priority once A3 and the smaller output cap are in.
7. **Compaction triggers early (32k chars, about 11-13k tokens) and is extractive.** Codex compacts at 90% of the window, and Terminus when fewer than 8k tokens are free (`T2/terminus_2.py:278, 1093`). Both use a model-written summary.
   - Probably fine, because each stateless CLI call is paid in full. Measure whether facts get lost, and switch to A6-style summaries if they do.
   -

## C. Looked at and not recommended

- **tmux or PTY sessions** (Terminus; Codex `write_stdin`). They would need one session per parallel step. A2 already covers the main benefit.
- **Codex `apply_patch`.** Its fuzzy matching is in `apply-patch/src/seek_sequence.rs:3`. Revisit only if traces show `sed` edit failures.
- **AGENTS.md handling.** Terminal-Bench containers have none.
- **`update_plan`.** Our DAG replaces it.
- **Terminus's three-call summary.** Too costly for cheap tiers.
