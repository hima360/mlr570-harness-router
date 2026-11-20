# Why v4 did not beat v2.8 (dev study, 2026-11-19)

Data: v4d6 (setup ocxv4, opencodex backend, 12 dev tasks x 2) vs dv8 (v2.8) and dv13 (v3.5), both on the Droid
backend, 1 run per task. Cost = tokens x list price. Scripts: `scripts/v4_assess.py` (per task),
`scripts/v4_stages.py` (per stage). Evidence: Verified locally. Numbers below are at 21 of 24 v4 runs; the
last 3 (payments r1 rerun, session-window r2, vf2 r2) can move the solve rate to between 0.38 and 0.50 at most.

## Headline

| | runs | solve rate | cost/run | takeover rate | cost/solve |
|---|---|---|---|---|---|
| v2.8 (dv8) | 12 | 0.50 | $0.92 | 0.92 | $1.85 |
| v3.5 (dv13) | 12 | 0.50 | $0.69 | 0.83 | $1.37 |
| v4 (v4d6) | 21 | 0.43 | $1.07 | 0.86 | $2.51 |

The gap is about one solve in twelve, so it is within noise. v4 is not a clear loss, but it shows no gain, and
v4's goal (25% fewer takeovers at equal solves) was missed: takeovers fell 7%.

## Per stage (medians)

| stage | v2.8 | v4 | what changed |
|---|---|---|---|
| GLM builder: calls | 22 | 12 | half the actions |
| GLM builder: output tokens | 45k | 118k | 2.6x more, almost all hidden thinking |
| GLM builder: minutes | 7 | 39 | 5.6x longer |
| builders that built nothing (judge's words) | 0 of 12 | 5 of 21 | new failure mode |
| judge sends to Opus | 11 of 12 | 18 of 21 | about the same |
| Opus takeover: calls | 8 | 16 | 2x |
| Opus takeover: input tokens | 153k | 311k | 2x |
| takeovers that solved | 5 of 11 (45%) | 8 of 18 (44%) | same |

## Why: three causes, in order of weight

1. **The GLM builder got weaker on the new backend (main cause).** 4 of the 5 builders that built nothing
   spent 97k-206k output tokens in 8-26 calls over 27-56 minutes: 3-6 calls of 15k+ tokens of thinking each,
   then a time-out or empty replies cut off by the 32k cap. Under Droid the same model built something on every
   task. Opus then starts from zero, which doubles takeover length and cost while its success rate stays 45%.
   Likely mechanism (unverified): GLM-5.x expects its earlier reasoning replayed each turn (opencodex lists it as
   "GLM interleaved thinking"); our backend sends back only the visible text, so GLM re-derives the task every
   turn. Also contributes: the Z.ai API defaults to max effort (fixed) and streams at ~59 vs ~135 tok/s.
2. **A1 (builder check script) did not change the judge's decisions.** With all checks green the judge still
   sent 8 of 11 builds to Opus, and 4 of those 8 then solved, so the judge was often right to distrust the
   checks. Checks written by the builder encode the builder's reading of the task, including its mistakes.
   They also cost builder and Opus turns: v4 takeovers spent commands extending /tmp/phase2_checks.sh.
3. **Run-to-run noise.** The same task costs 2-6x more from one run to the next, and solves flip on
   mvcc, fin-saccr, react. Twelve tasks cannot separate a one-solve difference.

v4's other two changes were inert: the judge's out-of-turns rule never fired, and the tool probe changed no
decision we could see.

## What to bring from v4 into v2.8

Keep (infrastructure; needed for any run on opencodex, no policy change):
- GLM reasoning effort pinned to `high` (Z.ai API default is `max`; Droid used `high`).
- Streaming, retry with backoff on rate limits and 5xx, and retry on empty broken streams (d5aea2b).
- Z.ai quota gate (stop at 80% weekly, wait out peak) and the supervisor's infra rules (broken streams,
  provider errors, zero tokens) so infrastructure failures are rerun, not scored.
- Builder wall time scaled to backend speed (1500 s on Droid = 3300 s on opencodex). Caveat: tasks with
  their own clock (kv-live's load generator) can expire; a command-count limit is the safer budget.

Drop:
- The 32k per-call output cap: it turns long thinking into empty replies (about 10% of GLM calls).
- A1 check scripts and the judge's check section: no fewer takeovers, extra turns.
- Judge out-of-turns = fix and the tool probe: never mattered; keeping them only adds untested surface.

Test before trusting either result (one change, small run):
- Replay GLM's `reasoning_content` in the history (phase2/ocx.py). If it brings builder output back near
  45k tokens and 7 minutes, it fixes cause 1 for v2.8 too.
- ocx28: v2.8 on opencodex, 12 dev tasks x 1. Without it, v2.8's held-out numbers (Droid) and anything new
  (opencodex) are not on the same backend.

## v4.1 result and decision (2026-11-19, 14:45)

v4.1 = v2.8 policy on opencodex plus the "keep" list above, GLM reasoning replay (Responses route), no output cap,
and the judge fixes (5ff89e4). Batch v41d2, stopped by decision after 6 of 12 tasks. Evidence: Verified locally.

| same 6 tasks | solves | cost (list price) | builder out tok (median) | builder min | sent to Opus | takeover in tok |
|---|---|---|---|---|---|---|
| v2.8 (dv8, Droid) | 3 | $4.21 | 15k | 3 | 5 of 6 | 99k |
| v4.1 (v41d2, opencodex) | 2 | $5.92 | 39k | 14 | 6 of 6 | 214k |

Reasoning replay fixed v4's empty builders (6 of 6 submitted) but GLM on opencodex is still ~4x slower than on
Droid, the judge still sends every build to Opus, and takeovers are 2x longer. No sign v4.1 beats v2.8.

**Decision: v2.8 (setup takeover28) is pinned as the final Phase 2 harness.** v4 and v4.1 are reported as tested
and rejected. Their backend fixes (ocx.py retries, effort pin, supervisor infra rules) stay in the code because any
future run must use opencodex. Open question for the report: v2.8's held-out numbers are on Droid (ended
2026-11-03); ocx28 (v2.8 on opencodex, 12 dev tasks) would separate backend from design. Not run.

## v2.8 on Droid vs v2.8 on opencodex, all 12 dev tasks (2026-11-19, 18:37)

v41d2 resumed and finished as the ocx28 comparison (v4.1 is v2.8's policy; only backend and judge-runaway fixes
differ). 1 run per task, no infrastructure-ended runs. Evidence: Verified locally.

| | solves | cost | cost/run | cost/solve | builder out tok | builder min | sent to Opus | takeovers solved | takeover in tok |
|---|---|---|---|---|---|---|---|---|---|
| v2.8 Droid (dv8) | 6/12 | $11.09 | $0.92 | $1.85 | 45k | 7 | 11 | 5 of 11 | 153k |
| v2.8 opencodex (v41d2) | 3/12 | $13.31 | $1.11 | $4.44 | 78k | 24 | 12 | 3 of 12 | 214k |

Lost on opencodex: fin-saccr, react-lead-form, risk-scorer (fin-saccr and react flipped run to run before).
No task was gained. Two things changed, not one: the GLM builder is slower and does less (cause 1 above), and the
Opus takeover solved 25% of its tasks instead of 45% while reading 40% more input. With 12 tasks and three known
flippers this is not proof of a backend effect, but it points the same way as v4d6.

Consequence for the report: v2.8's held-out numbers (Droid) stand as measured; any new run on opencodex is a
different system and must be compared only with other opencodex runs.
