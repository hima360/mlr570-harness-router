# Droid deep check (2026-10-25, Claude Opus 5.5)

Sources were fetched raw with curl and grepped, not summarized by a model. Abbreviations used below:
- TB = https://factory.com/news/terminal-bench (Sep 25 2025, Terminal-Bench 1 core v0.1.1, Opus 4.1)
- RT = https://factory.com/news/model-routing-belongs-in-the-harness (Aug 24 2026)
- PB = https://factory.com/news/what-it-takes-for-coding-agents-to-complete-large-software-tasks (Aug 27 2026)
- CC = https://factory.com/news/compressing-context
- EC = https://factory.com/news/evaluating-compression
- FR = https://factory.com/news/factory-router-inference-savings
- Docs pages are named by path, for example `docs.factory.com/droid-exec/overview.md`.
- Also checked: local `droid exec --help` and `~/.factory/droids/*.md`.

Code facts are Source inspected at HEAD c456b1f. That commit already adopted several of these practices.

## A. Verification of docs/research/droid-practices.md

| # | Verdict | Checked at | Notes |
|---|---|---|---|
| 1 | Quote CONFIRMED, conclusion WRONG | `droid exec --help` | The help text has "Session continuation (preserves context, no message replay)". Its session-flag text says it "Loads conversation history for context but does NOT replay old messages in output". So "no replay" is about **output**. The model still gets the whole history, so `-s` does not shrink 40k prompts. What it can buy is a warm prompt cache (see N2). `droid.py complete` is now at line 29, not 26. |
| 2 | CONFIRMED | TB | "The system notifications tier proved particularly crucial". Partly adopted: `cli_models.remind` adds a reminder on every call. The corrective notice after a zero-command reply is not built. |
| 3 | Quotes CONFIRMED; code claim STALE | TB | "presenting this system information as shell command output". Workers already get `orchestrator.env_block` (orchestrator.py:70), but the probe is only `pwd && ls -la \| head -50`. Droid's list is "programming languages, git repo contents, environment variables, and running processes". |
| 4 | Quote CONFIRMED; design claim CONFIRMED; code nuance | PB | "never authors it, runs it, or sees its cases or raw output". COORDINATION_DESIGN.md:114 does plan to show workers "the check that will judge it". Today `INSTANCE_TEMPLATE` hides the check, but `Orchestrator._handoff` gives the retry worker the raw check output ("The step's check said:"). PB caveat: the wall exists because the cases are "only a sample". Both PB roles ran the same model. |
| 5 | CONFIRMED | RT | "our default validator comes from a different model family". Not adopted. |
| 6 | CONFIRMED; adopted | TB | `worker.SLOW_COMMAND_SEC = 15`. |
| 7 | Quote CONFIRMED; NOT adopted | TB | "using short default timeouts led to better average performance". `config.worker_command_timeout` is still 180. The model also has no way to opt into a longer timeout, which TB describes ("explicitly opts into longer timeouts"). |
| 8 | CONFIRMED; adopted | TB | The quoted crossing-off passage and the recency rationale (citing Lost in the Middle) are both accurate. Implemented as `cli_models.remind`. |
| 9 | CONFIRMED | TB, RT | FIND_AND_REPLACE vs V4A; "model-specific tool scaffolding". RT adds that the harness picks the editor per model. |
| 10 | CONFIRMED with caveat | `droid-exec/overview.md`, `--help` | `"maxOutputTokens": 16384` appears, but only in a BYOK `customModels` example. `droid exec` has no output-cap flag for built-in models (config.py:61 says so). `-r` "(defaults per model)" is CONFIRMED, and `droid.py` passes `-r` only for low-effort steps. |
| 11 | Quote CONFIRMED; code claim STALE | `droid-exec/overview.md` (Exit behavior) | "Exit `0` means Droid finished, not that the task succeeded." `planner.parse_plan` now rejects steps with no check. The trust path still exists in `verifier.verify` (lines 12-14), and `planner.single_step_plan` (fallback, planner.py:144) builds a step with no check, so it still reaches that path. |
| 12 | CONFIRMED | `harness/subagents.md`, RT, local `user-testing-flow-validator.md` | "fresh context window", "a single return value", "cannot spawn its own subagents", "reports back instead of prompting". "instead of the full session history" is in RT. The status set `pass\|fail\|blocked\|skipped` is in the local file. |
| DNC-1 permissions | CONFIRMED | TB | "with all permissions skipped (as the environment was sandboxed)". |
| DNC-2 2.37x | CONFIRMED but misframed | RT | 2.37x is the **modeled fully uncached** cost at turns 151-200. Real cache-aware routing stays at 0.19-0.28x of all-frontier. The lesson is "keep the cache warm", not "never switch". |
| DNC-3 14x | CONFIRMED | PB (text and gdal chart data) | "14 times the credits"; chart shows 216M to 3.00B; 36% to 90%. "per-milestone scrutiny and user-testing" is real, but it comes from `missions/reference.md` (`skipScrutiny`, `skipUserTesting`), not from PB. |
| DNC-4 multi-model | CONFIRMED | TB | "Droid exceeds multi-model agents' performance". This is a 2025 Terminal-Bench 1 result. RT (2026) now routes within sessions. |
| DNC-5 skills | CONFIRMED | `droid-exec/overview.md` | `--disable-builtin-skills` exists and is inherited by child sessions. |

## B. New practices

### N1. Anchored, incremental compaction with fill and drain lines (High value, low-medium effort)

**What Factory does**
- Keep one persistent summary, and "summarize only the newly dropped span and merge it" (CC).
- Two thresholds: T_max ("fill line") and T_retained ("drain line"). The gap between them sets how often compaction runs.
- Compacting too often causes "Frequent prompt cache invalidation" (CC).
- The summary must keep four things: session intent, play-by-play, artifact trail, and breadcrumbs (paths and identifiers to re-fetch).
- EC: fixed sections ("session intent, file modifications, decisions made, and next steps") scored 3.70 overall, against 3.44 for Anthropic and 3.35 for OpenAI, over 36,611 messages.
- The artifact trail was the weakest dimension for every method (2.19-2.45). EC suggests "explicit file-state tracking in the agent scaffolding".

**What we do today.** `worker.compact_messages` rebuilds the fold from scratch on every call once over budget. It slides the verbatim window by one turn per call and rewrites `head[1]`. That changes the prompt prefix on every call, and the fold keeps only `ran:/got:` lines.

**Change in `worker.compact_messages` and `cli_models.make_text_model`**
- Keep `(anchor_index, summary_text)` on the `CliTextModel` instance.
- Compact only when total > `context_budget_chars`, then drain to about 0.6 of it. Calls in between reuse the same folded prefix.
- Fold only the newly dropped turns into the stored summary.
- Give the summary fixed sections:
  - `FILES WRITTEN` (harness-derived from `>`, `tee`, `sed -i`, `cat >` in commands, or `find / -newer` a marker file)
  - `FACTS FOUND` (outputs.txt lines)
  - `LAST FAILURE`
  - `NEXT`

### N2. Prompt cache is unused (High value, medium effort)

**Evidence (Verified locally, one trial)**
- `jobs/shadow-relay-ladder-1025c/.../result.json` has `n_cache_tokens = 0` against 1,927,222 input tokens. Cost was $2.2566.
- Repricing the trace reproduces that cost: Sonnet input alone is $1.20, and input is 59% of spend.
- Prices in this run: Haiku cached is 0.01 vs 0.1 input; Sonnet cached is 0.1 vs 2.

**Why it matters.** RT: cached prefix "typically charge a tenth of the fresh-input rate" and "Staying put is itself a routing decision".

**Likely cause (Unverified).** `claude_code.complete` pipes the whole conversation as one stdin message (`serialize(rest)`), so no earlier turn boundary can match the cache. `droid.py` does the same with `-f prompt.txt`.

**Change**
1. Log `cached_tokens` in `llm.record_call` trace rows. Today the `llm_call` rows have no cache field.
2. Try `claude -p --input-format stream-json` with real turns, or `droid exec -s <id>` sending only the new turn, and measure the cache-read share.
3. Combine with N1, which keeps the prefix stable.

### N3. Check on submit, with a bounce (High value, low effort)

**What Factory does.** Droid hooks: `Stop` can "Require final checks or continue with follow-up instructions", and `SubagentStop` can "Validate subagent output or request more work". Exit code 2 feeds stderr back (`harness/hooks.md`).

**What we do today.** A failed check ends the attempt. The next try is a fresh worker: new prompt plus handoff plus a triage call.

**Change in `worker.HarborEnvAdapter.execute`**
- When the SUBMIT line arrives, run `step.check` there (pass `step` into the adapter).
- If the check fails, return "[harness] not done: acceptance check failed (exit N); published outputs: ..." instead of raising `Submitted`.
- Allow at most one or two bounces, then submit anyway.
- Do not show the check command, so the PB wall holds.
- Log a `bounced` count per attempt in `orchestrator._attempt` so routing accounting stays honest.

### N4. End an attempt early when it is struggling (High value, low effort)

**What Factory does.** RT: "Repeated failures on the same fix can change the next model choice". Router FAQ: "If the selected model struggles with a task ... it moves the session to a more capable model" (`model-independence/factory-router.md`).

**What we do today.** We escalate only after the 25-40 action limit or a failed check. In trace 1025c, s4 used 25 Haiku calls and then 21 Sonnet calls.

**Change.** In `cli_models.CliTextModel._query`, or in the adapter, count consecutive non-zero return codes, repeated identical commands and guarded or format-error replies. At a threshold such as 4, raise a new `Struggling` exception. Add it to `abort_exceptions` but not to `INFRA_EXITS`, so `_run_step` escalates and counts it as a router error. This switches models at the attempt boundary, so it stays consistent with DNC-2.

### N5. Score routing the way Factory does (High value for the paper, low effort)

**What Factory reports**
- RT: "Routed runs reach 99% of the frontier model's pass rate on Terminal-Bench 2 ... at about 20% lower cost per successful run".
- Production savings are measured against "the same session priced as if every call had used the frontier model".
- The numbers drift by date: RT gives 58% aggregate and 76% median. FR and the Router docs give 63% aggregate, a 72.5% median, and 84% of sessions saving at least half.
- RT also gives a per-session table (166 turns, 81%; 67 turns, 0% "the right answer").
- Routing records are kept as (state, model, result) "to refine the policy".

**Change in `jobs.py` and `ledger.py`**
- Report pass rate as a % of always-Opus, and cost per successful run.
- Add a counterfactual: reprice each routed trace's tokens at the top tier's prices, so savings can be reported per trial without extra runs.
- Emit per-step (route features, tier, attempt outcome) rows for `train_head.py`.

### N6. Short default timeout that the model can raise (High value, low effort)

This closes the gap left by #7.
- Set `worker_command_timeout` to about 60 s.
- In `HarborEnvAdapter.execute`, parse an optional first line `# timeout: <sec>` (capped at about 600) and pass it as `timeout_sec`.
- Mention it once in `INSTANCE_TEMPLATE` next to the existing nohup hint.

TB: the short default "just nudges toward faster iteration".

### N7. Structured worker handoff (Medium value, low effort)

**What Factory does**
- Mission handoffs carry `commitId`, `repoPath` and `skillFeedback.followedProcedure` and `deviations`.
- Reviewers read a "transcript skeleton" from `worker-transcripts.jsonl` (local `scrutiny-feature-reviewer.md`).
- The local `glm-worker.md` droid ends with "files changed ... what you verified and how, what is still unverified".

**What we do today.** The submit line asks for "one or two sentences".

**Change.** In `INSTANCE_TEMPLATE` and `outputs_hint`, require `changed=`, `verified=` and `unverified=` lines in outputs.txt. `orchestrator._incident` and `_handoff` then show those lines before `attempt_digest`. That gives the fixer facts instead of a digest of raw turns.

### N8. Team-wide environment knowledge (Medium value, low effort)

**What Factory does**
- Scrutiny reviewers record `sharedStateObservations` (convention, skill, service and knowledge gaps) so later workers do not rediscover them. Examples: env vars, quirks, and "commands or start services that should be in `services.yaml`".
- Validators "cannot weaken" the standard: "Generated guidance cannot authorize new acceptance requirements" (local `user-testing-flow-validator.md`; PB "cannot weaken or revise it").

**Change**
- Let workers publish `env.<name>=...` facts, for example `env.python=python3` or `env.no_internet=1`.
- `blackboard.summary_for` shows all `env.*` facts to every step, appended to `env_block`.
- Keep the error handler unable to edit `step.check`. That is true today, so add a test pinning it.

### N9. Background services that outlive the step (Medium value, low effort)

TB: Droid has a "controlled background-execution primitive" so a server keeps running "for tests to hit later", and every spawned process "is tracked for later cleanup".

**Change**
- In `INSTANCE_TEMPLATE`, say explicitly that services the task needs must be started with `setsid nohup ... &` and left running.
- In `HarborEnvAdapter`, record such commands in `/tmp/steps/<id>/services.txt` so later steps and the fixer know what is running.

Terminal-Bench grades after the agent exits, so a server killed with the shell fails the task.

### N10. Richer bootstrap probe (Medium value, low effort)

This finishes #3. Replace `Orchestrator._env_summary` with one combined probe:
- `pwd; ls -la`
- `git status -s | head`
- `which python3 pip node gcc make cargo go`
- `env | grep -v -i -E 'key|token|secret|pass' | sort | head -40`
- `ps -eo pid,cmd | head -20`
- `cat /etc/os-release | head -2`

Show it as already-run output. TB says this nudges toward "alternatives it wouldn't have considered".

### N11. Check-failure directives instead of raw output (Low value, low effort)

PB loop: the validator "groups failures by root cause". The orchestrator "rejects noise" and issues a directive at the feature level, and "clustered findings cross the wall".

**Change.** In `_run_step`, when triage returns `retry` or `escalate` with a note, put the fixer's note in the handoff **instead of** the raw `check_output` in `Orchestrator._handoff`. That keeps the check opaque.

### N12. Cost model for validation (Low value, low effort)

`missions/planning.md` estimates `total runs ≈ #features + 2 * #milestones`, with validation only at milestone ends. Missions FAQ, on parallelism: "does it actually produce better results than sequential execution? We are testing this."

For long serial chains, this supports the planner rule in `PLANNER_PROMPT` ("prefer fewer, larger steps"). Add `plan_shape` metrics (chain depth) to the cost-per-success analysis to test it, rather than forcing parallelism.

## Not worth copying (new)

- **LLM routing calls and admin text guidance** (FR: "model-selection calls", "routing guidance and rules"). Laya plus Arch-Router is our research variable, so a second LLM router would confound it.
- **Missions' QA tooling** (`tuistory`, `agent-browser`). Terminal-Bench has hidden tests, not a user surface.

Evidence notes: the trace numbers come from a single trial, older than c456b1f's output cap (Historical). The N2 cause is a hypothesis (Unverified). No code was changed.
