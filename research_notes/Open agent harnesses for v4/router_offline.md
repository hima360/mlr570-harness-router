# A5 offline router test: early GLM signals vs "always cascade"

Date: 2026-11-18. Evidence: Verified locally (scripts below run on `jobs/`, read-only, no paid calls).
Scope: dev runs only. Prefixes `dt1` (dev tasks only, same filter as `our_dev_traces.md`) and `dv6`..`dv15`.
No `ho*` or `v4d1*` job was opened.

## Verdict: drop A5 as a v4 router

No simple router beat always-cascade on cost **at equal solves** under leave-one-task-out CV. The best ones cut
cost by 40-50% but lose 2-3 of 20 solves. The A5 keep rule in `docs/V4_PLAN.md` is not met. The perfect router
would keep all 20 solves for $9-16 instead of $38. So there is money to save, but the early signals we log cannot
find it.

## Data and how each route's outcome is estimated

Evaluation units: 42 valid v2.8-family runs (Opus judge + Opus takeover): takeover28 (12), 29 (9), 31 (7),
34 (2), 35 (12), on 12 tasks. In 9 runs the judge shipped; in 33 an Opus takeover ran. Runs with a GLM-5.3 takeover
(v3.0), a Sonnet takeover (v3.7) or an operator stop, and one VerifierTimeout run, are left out.

| route | solve | cost |
|---|---|---|
| cascade | observed | observed run cost |
| GLM only | judge shipped: observed (exact, 9 runs). Judge said fix: **not observed** (33 runs). Three estimates: OPT = the task's dt1 GLM-alone result (n=1 per task); POOL = mean of every GLM-only outcome on the task (dt1 glm + runs the judge shipped with no takeover; n=1-7 per task); PESS = 0 (trust the judge's "fix") | observed builder cost (+ v3.1 spec call) |
| Opus direct | dt1 Opus-alone result on the task (**n=1 per task**) | GLM cost of the first k commands + dt1 Opus-alone cost |
| stop | 0 | GLM cost of the first k commands |

Features, from the first k = 3, 5, 10 GLM builder commands: failure rate, "command not found", tracebacks,
timeouts, ran tests, files touched, wrote tests, mean command time, mean model-call time, mean output tokens,
mean characters per command and per output, submitted before k. Task-level: task text length, number of existing
tests found by the harness.

Models: decision stump (one feature, one threshold, a route on each side), and L2 logistic regression (P(solve)
per route, cost per route = training mean). Both maximise solves − λ·cost, λ = 0.25, 0.5, 1 (a solve worth
$4, $2, $1). CV: leave one task out (12 folds); all runs of the test task are held out together.

## Numbers (42 runs; solves are expected values where a route is estimated)

| policy | solves | $ |
|---|---|---|
| always cascade (baseline) | 20 | 38.02 |
| always Opus direct | 15 | 38.25 |
| always GLM only | 11.0 OPT / 11.2 POOL / 5 PESS | 2.97 |
| oracle, same solves as cascade per run, cheapest route (k=5) | 20-21 | 9.23 OPT / 9.23 POOL / 15.74 PESS |
| oracle, most solves (k=5) | 22-23 | 9.72 OPT / 16.50 PESS |
| best LOTO 4-way: stump, k=10, λ=0.5, POOL | 17.8 | 18.72 |
| best LOTO 4-way under PESS: stump, k=10, λ=0.25 | 17 | 27.80 |
| LOTO logistic, 4-way, best (k=10, λ=0.25, POOL) | 14.8 | 17.61 |
| **fully observed** cascade-or-stop, LOTO logistic, stop if P<0.2, k=10 | 18 | 22.10 (7 stopped) |
| fully observed cascade-or-stop, LOTO stump, k=10, λ=0.5 | 17 | 18.66 (11 stopped, 3 were solves) |

Every other k/λ/estimate combination was worse (full grid: rerun `r2_router_eval.py` and `r3_router_stop.py`). At k=3 and
k=5 the learned routers lose 7-12 solves. Larger λ just collapses to "always GLM" or "stop".

The cascade-or-stop rows need no counterfactual at all, so they are the most trustworthy: the best rule saves
$16 but loses 2-3 solves of 20.

## Single best feature: mean characters per GLM command (first 10 commands)

In-sample AUC for the cascade's solve: 0.68 (k=10). The stump picked it in 11-12 of 12 folds. Short commands mean
"stop".
It is a task fingerprint, not a per-run signal. heat-pump (0.16-0.21 k chars), kv (0.26-0.41) and vf2 (0.31-0.77)
are long, exploratory, never solved and expensive; stopping them is where the saving comes from. risk-scorer
(0.68-2.0) looks the same and is solved 3/3 at $1.8-4.3, so the rule loses it. Within a task the feature does not
separate solves from failures. With 12 tasks, three plateau tasks are the whole effect.
Text-only signals (task length, tests found) are at chance (AUC 0.46-0.5): this agrees with the published
"text-only routers near chance", but the published "early turns predict far better" does not show up here.

## Caveats
- n is small: 42 runs, 12 tasks, 9 exact GLM-only outcomes, Opus direct from 1 run per task.
- The headroom (oracle) mostly comes from per-task knowledge: skip the plateau tasks (cargo, heat-pump, kv,
  session, vf2: 0 cascade solves in 15 runs) and send GLM-solvable tasks to GLM. A router would have to recognise
  those from early behaviour on unseen tasks, and these features do not.
- POOL/OPT are optimistic for "fix" runs (the judge flagged them); PESS is a lower bound.

## What's next
Remove A5 from the v4 build list in `docs/V4_PLAN.md` (keep it as a reported negative result for the course
question). If the group wants to retry it, the only lead is a "stop" rule for long exploratory plateau tasks.
That needs more never-solved tasks in the dev set first; with 3 such tasks it cannot be validated.

## Scripts (in `scripts/`, run with `.venv/bin/python`)
`extract.py` (writes `runs.json`) → `r1_router_features.py` (writes `units.json`) → `r2_router_eval.py`
(4-way routes, oracle, LOTO stump and logistic) and `r3_router_stop.py` (fully observed cascade-or-stop, feature
AUCs).

## Change Record
Task: A5 offline router test
Linear: None
Update: No early-signal router (stump or logistic, k=3/5/10, LOTO over 12 tasks) matches always-cascade solves
at lower cost; best loses 2-3 of 20 solves for 40-50% less cost. Oracle ceiling 20 solves at $9-16 vs $38. Verdict: drop A5.
Evidence: Verified locally (scripts on dev jobs, 2026-11-18)
Next: Strike A5 from the v4 build list in docs/V4_PLAN.md and log it as a negative result.
Edited by: Claude Code (Anthropic, Claude Opus 5.5) — 2026-11-18T21:00:00+04:00
