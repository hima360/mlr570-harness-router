# Our dev traces: where the harness loses solves, time and money (evidence for v4)

Date: 2026-11-18. Evidence: Verified locally (scripts run on `jobs/`, read-only). Inferred points are marked.

## Scope and rules
- Runs used: prefixes `dt1` and `dv6`..`dv15` only. No `ho*` job was opened.
- `dt1` also ran held-out tasks. Only dev tasks were kept: the 12 tuning tasks from `docs/OVERNIGHT_LOOP.md` plus the
  `EARLY_USED` list in `scripts/draw_heldout.py` (never in a held-out draw). This kept 48 dt1 runs (16 tasks x 3
  setups) and dropped 66. All 82 dv runs are dev tasks. Total: 130 runs. 11 were stopped by the operator
  (`CancelledError`: dv14 x4, dv15 x5, dv9 x2). Most tables leave them out (119 valid runs).
- Prices (USD per M tokens, fresh input / cache read / output): Opus 5.5 4 / 0.20 / 20, GLM-5.3 Flash
  0.15 / 0.03 / 0.50. The task gave no price for Sonnet 5 or GLM-5.3, so those come from `phase2/list_prices.py`
  (2 / 0.20 / 10 and 1.40 / 0.26 / 4.40). The repo prices fresh Opus input at the cache-write rate ($5), so repo
  totals run about 5-8% higher (dt1 Opus alone: $16.11 repo vs $14.99 here).
- Trace quirk: `phase2/llm.record_call` drops zero values, so a missing `cached_tokens` means 0 cached tokens, not
  unknown data. It was read that way here.
- `tests` share = `phase2.results.tests_fraction`. For react-lead-form it reads 0 even on solved runs, so its
  partial credit is unknown.

Scripts (scratchpad, not in the repo):
`/private/tmp/claude-501/-Users-hima-Developer-mlr570-harness-router/ccf40450-bbb1-46b5-86a9-9dd8e373e506/scratchpad/`
`extract.py` builds `runs.json`. `a1_summary.py` covers versions and time, `a2_judge_takeover_cache.py` the judge,
takeovers, cache and latency, and `a2c_breaks.py` the causes of cache breaks. `a3_fail_tools.py` covers failure
types and commands, `a4_errors.py` failed commands, `a5_misc.py` dead containers and missing binaries,
`a6_matrix.py` the task x setup grid, `a7_phases.py` phase times and never-solved spend, and `a8_judge_cost.py`
the judge's cost. Run each one with `.venv/bin/python <script>`.

## 1. Per version (dev tasks; includes stopped runs; a1_summary.py)
| prefix | setup | version | runs | solves | $ | $/run | $/solve | median min/run |
|---|---|---|---|---|---|---|---|---|
| dt1 | glm | GLM alone | 16 | 3 | 1.50 | 0.09 | 0.50 | 10.1 |
| dt1 | glmreview27 | v2.7 | 16 | 3 | 3.72 | 0.23 | 1.24 | 20.6 |
| dt1 | opus | Opus alone | 16 | 5 | 14.99 | 0.94 | 3.00 | 9.6 |
| dv6 | glmreview26 | v2.6 | 5 | 3 | 1.04 | 0.21 | 0.35 | 18.8 |
| dv7 | glmreview27 | v2.7 | 5 | 1 | 1.06 | 0.21 | 1.06 | 22.0 |
| dv8 | takeover28 | v2.8 | 12 | 6 | 11.09 | 0.92 | 1.85 | 16.4 |
| dv9 | takeover29 | v2.9 | 12 (2 stopped) | 5 | 10.06 | 0.84 | 2.01 | 12.5 |
| dv10 | takeover30 | v3.0 | 11 | 2 | 6.40 | 0.58 | 3.20 | 17.2 |
| dv11 | takeover31 | v3.1 | 7 | 2 | 7.14 | 1.02 | 3.57 | 11.1 |
| dv12 | takeover34 | v3.4 | 2 | 1 | 2.29 | 1.14 | 2.29 | 23.3 |
| dv13 | takeover35 | v3.5 | 12 | 6 | 8.25 | 0.69 | 1.37 | 10.5 |
| dv14 | takeover36 | v3.6 | 4 (all stopped) | 0 | 0.46 | - | - | 1.6 |
| dv15 | takeover37 | v3.7 | 12 (5 stopped) | 2 | 4.48 | 0.37 | 2.24 | 11.9 |

The same 12 tuning tasks, valid runs only (a6_matrix.py):
- Opus alone: 4/12 for $11.89.
- v2.8: 6/12 for $11.09.
- v3.5: 6/12 for $8.25. This is the cheapest version at the top solve count on dev.
- GLM alone: 3/12 for $1.03.

Model spend over the 119 valid runs: Opus $57.69, GLM-Flash $7.56, GLM-5.3 $3.18, Sonnet 5 $1.81. In the
takeover-era runs ($47.93), the Opus takeover is $33.20 (69%), the judge $10.89 (23%) and the GLM builder $3.62 (8%).

## 2. Where time goes (a1_summary.py, a7_phases.py; 130 runs, 38.4 h of trial time)
- Agent execution: 34.3 h (89%). Verifier: 3.2 h (8.4%; median 22 s, p90 264 s, max 915 s). Environment setup:
  0.36 h (0.9%; median 4 s). Harbor copy and teardown: 0.5 h (1.4%).
- Model latency is the biggest part of agent time:
  - GLM-Flash builder calls: 17.0 h (44% of all trial time). n=2803, median 9.7 s, p90 49 s, median 735 output
    tokens per call.
  - Opus takeover calls: 3.1 h (median 12.8 s). Opus judge calls: 2.0 h (median 17 s, p90 50 s).
  - GLM-5.3 takeover (v3.0) was the slowest per call: median 24.8 s, p90 126 s.
  - Shell commands plus harness overhead: 6.1 h (16%).
- Dependency installs are not a time sink. There were 42 install commands in 31 of 130 runs. Only 6 ran longer than
  15 s, for 249 s in total. Only 2 takeover installs repeat the builder's work, and both were different packages.
  The most common install is `openpyxl` in fin-saccr-rwa (most runs). A dependency cache is not worth building.
- Commands that hit the 180 s limit: 28 (mostly the GLM builder). Commands of 120 s or more: 49. The slowest command
  types are python heredocs (3546 s), `cat` heredoc file writes (3149 s, inferred: chained runs after the write) and
  `sleep` polling (1632 s).

## 3. Judge ("senior_review", Opus) (a2_judge_takeover_cache.py)
- 85 valid runs were judged. Final verdict "ship": 23 runs, of which the verifier passed **9 (39% precision)**.
  Final verdict "fix": 62 runs, of which 22 were solved in the end (35%).
- Wrong ships are mostly near misses or partial work: cargo 0.70 (x4), mvcc 0.73 (x3) and 0.93, fin-saccr 0.92 (x2),
  data-anonymization 0.75, session 0.57, html-js 0.50, react 0.
- fin-saccr-rwa is the clearest case. v3.5 and v3.7 shipped it at 0.92 and failed. v2.8 and v2.9 sent it to "fix" and
  solved it; v3.0 sent it to "fix" and failed at 0.92. v2.7 and v3.1 shipped it and passed.
- The judge says "fix" on tasks GLM alone solved in dt1 (embedding-drift, mvcc, risk-scorer) in 10 runs, and 7 of
  those were solved anyway. Inferred, not provable without a counterfactual: some of these takeovers may have been
  unneeded. Example: dv8 mvcc, where the takeover cost $0.24 and the run ended at 0.93.
- Judge cost: $13.88 in total, median 4 calls per review, median $0.13 per run, and a median 41% of each judged run's
  cost. Output tokens per judge call: median 770, p90 3934. The reply text is a short JSON `explore`/verdict.
  Inferred: most of that output is hidden reasoning.

## 4. Takeovers (a2_judge_takeover_cache.py)
- 43 takeovers in valid runs. 17 solved (40%). Cost $33.20 of the $44.18 those runs cost, about $1.95 per
  takeover solve.
- Ends: Submitted 39, ProviderUnavailable 3, RepeatedFormatError 1.
- Length in calls: solved runs have a median of 13 (4-43); failed runs a median of 9.5, with a long tail of
  42, 51, 56, 57, 57 and 59.
- heat-pump-warranty: 6 takeovers, 0% tests every time, $8.73 (26% of all takeover spend). Overall the task cost
  $12.09 over 9 runs and was never solved by any setup, Opus alone included ($2.30).
- Share of run cost spent on the takeover: v2.8 80%, v2.9 84%, v3.0 64%, v3.1 76%, v3.5 71%, v3.7 74%.
- Failed takeovers that came close: vf2 0.98 (x2, 42 and 21 calls), mvcc 0.93, fin-saccr 0.92, embedding 0.91 and
  session 0.86.

## 5. Opus prompt cache (a2_judge_takeover_cache.py, a2c_breaks.py)
- Cache hit rate (cached / input tokens), by role:
  - Opus takeover: 82.1%. Fresh input $11.85, cache reads $2.72, output $13.64.
  - Opus judge: 68.4%, with no session. The first call of a review has a median hit of 0.37; later calls 0.91.
  - Opus-alone worker: 88.3%.
- Cache breaks, priced as fresh input:
  - First call of each role (cold start): 160 calls, $4.10. The judge's are $2.96 and the takeover's $0.77.
  - The harness starting a new session part-way through a role (`session_budget_chars` 80k): 45 calls, $2.36.
    Spread over Opus alone, v2.8, v2.9 and v3.1. **None in v3.5/v3.7 (300k budget)**: already fixed.
  - Resumed session with almost no cache and the input still growing: 39 calls, $3.07, only in v2.9, v3.1 and v3.4.
    The cause is not visible in the traces. v3.4 used low effort on every resume (inferred: the effort change
    invalidates the cache).
  - Resumed session where the input shrank: 9 calls, $0.79. Inferred: Droid compacted the context on its side.
- Net: on the current cache setup (v3.5 and later), cache waste is close to zero beyond cold starts. The cache is no
  longer a lever.

## 6. Failure types of unsolved runs (a3_fail_tools.py; 91 unsolved of 130)
| category | runs | share |
|---|---|---|
| partial: 50-80% of tests | 32 | 35% |
| far: <50% (4 are react, where the share is unknown) | 19 | 21% |
| near miss: 80% or more of tests | 15 | 16% |
| stopped by operator | 11 | 12% |
| provider unavailable (Droid) | 5 | 5% |
| Harbor ValueError (cumulative-layout-shift, every setup: broken task) | 3 | 3% |
| builder hit its time limit, no takeover (dt1 only) | 3 | 3% |
| VerifierTimeoutError (react-lead-form at 915 s, x2) | 2 | 2% |
| takeover ended by RepeatedFormatError | 1 | 1% |

Zero-token runs: none.

**Plateaus.** The same test share recurs in every setup, Opus alone included (a6_matrix.py, a7_phases.py):
- cargo-flight-dispatch: 0.70 in 8 of 9 runs.
- session-window-debug: 0.57 in 6 of 7.
- vf2-speedup-networkx: 0.98 in 5 of 5.
- html-js-filter: 0.50 in every unsolved run.
- heat-pump-warranty: 0 in 9 of 9.

The 5 dev tasks no setup ever solved took $27.39 of $68.14 dev spend (40%), and $19.17 of $47.93 in the
takeover-era runs. Inferred: the same checks fail whatever the model and however much effort, so these are
shared misreadings of the spec or untested requirements, not a lack of model strength. The v3.1 spec card did not
move cargo (still 0.70).

## 7. Tool use (a3_fail_tools.py, a4_errors.py, a5_misc.py; 4178 commands)
- Most common commands: python3 heredoc or `-c` 898, `cat` heredoc file writes 583, `sed` 447, `echo` 296,
  `printf` 203, `mkdir` 171, `for` loops 170, `cat` 160, `grep` 144, `timeout` 103.
- Failure rate (non-zero return code): GLM-Flash 15% (469/3096), Sonnet 5 14%, Opus takeover 9%, Opus alone 7%,
  GLM-5.3 7%.
- What the failures were: python tracebacks 140; rc=1 with almost no output (grep with no match, tests) 161;
  timeouts 35; command not found 28; missing file 19; syntax errors 6.
- Missing binaries: `file` 13, `ps` 10, `python` 9 (9 runs; only `python3` exists), `python3` 5, `bc`/`pgrep` 2 each.
- Format errors (a reply with no action): GLM-Flash 53, about 1.9% of its calls; Opus 7; Sonnet 4.
- Identical command repeated within one agent: 24 times. Small, except in dead containers (next point).
- **Dead container:** 160 commands went to a container that had already stopped (`service "main" is not running`),
  all in the 11 operator-stopped runs. Examples: dv14 embedding 43, dv14 heat-pump 39, dv9 kv (Opus) 31. The agents
  kept going, `echo alive` and repeated submit attempts included. Those 11 runs cost $2.23.

## 8. Cheap improvements this evidence supports (each still needs the red team)
1. **Judge ship precision (WHEN TO ESCALATE).** "Ship" is right 9 of 23 times on dev. Wrong ships are partial or
   near-miss work, and v3.5 lost fin-saccr this way. Matches the held-out finding in `OVERNIGHT_LOOP.md` (06:00,
   ship right 0/8). The judge never runs the task's own checks; "tester as evidence" (ideas doc section 3) is the
   matching idea.
2. **Stop on a dead container (harness hygiene).** After 2 straight `service ... is not running` or exec-exception
   outputs, end the attempt as infra. Seen in 160 commands and 9 runs. The dollars are small ($2.23 in all), but it
   protects the run budget and keeps logs clean.
3. **Environment facts in the bootstrap (WHAT THEY KNOW).** Show whether `python`, `file`, `ps` and `pip` exist.
   47 "command not found" messages in the outputs. A small gain (inferred). The general form is: list the missing tools.
4. **Judge cost.** The judge is 23% of takeover-era spend ($10.89). A median of 4 Opus calls per review, about 770
   output tokens each. Inferred lever: cheaper exploration before the verdict (GLM gathers the facts, Opus decides).
   Risk: worse verdicts, when precision is already the problem.
5. **Not worth doing:** a dependency or install cache (249 s across all runs), more cache work (fixed in v3.5), or a
   takeover length cap (dropped before, in v3.8: long takeovers are solves, up to 43 calls).
6. **Plateau tasks** take 40% of dev spend. No cheap fix is supported. A rule like "stop when the share has not moved"
   needs hidden test shares, which the harness cannot see (v3.3 and v3.8 red team).

## What's next
Pick between ideas 1 and 4 for v4 (both touch the judge) and send the chosen one to the `harness-red-team` agent with
sections 3 and 4 above as its evidence. Idea 2 (stop on a dead container) can go in behind a default-off switch with
a unit test, without a paid run.

## Change Record
Task: Dev-trace failure analysis for v4
Linear: None
Update: Measured solves, cost, time, judge, takeover, cache, failure and tool patterns across dt1 (dev tasks only) and dv6-dv15; no held-out jobs opened.
Evidence: Verified locally (scratchpad scripts on jobs/, 2026-11-18)
Next: Red-team "judge ship precision via tester evidence" vs "cheaper judge exploration" as the v4 candidate.
Edited by: Claude Code (Anthropic, Claude Opus 5.5) — 2026-11-18T18:40:00+04:00
