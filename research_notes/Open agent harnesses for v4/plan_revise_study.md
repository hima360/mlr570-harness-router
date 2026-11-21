# Plan, execute, revise, fix: when each helps on our dev runs

Date: 2026-11-18. Evidence: Verified locally (scripts run on `jobs/`, read-only). Inferences are marked "(inference)".

## Scope
- Same filter as `our_dev_traces.md`: `dt1` (dev tasks only: the 12 tuning tasks plus `EARLY_USED` from
  `scripts/draw_heldout.py`) and `dv6`..`dv15`. 130 runs, 127 with a trace. **116 valid** = traced and not
  operator-stopped. No `ho*` job was opened.
- One slip to disclose: a `grep -c` over `dt1-glmreview27-*` worker logs touched dt1 runs of held-out tasks
  (counts of two harness strings only). None of that output is used here; every number below comes from the
  filtered table.
- Prices per M tokens (fresh / cache read / output): Opus 5.5 4 / 0.20 / 20, GLM-5.3 Flash 0.15 / 0.03 / 0.50;
  Sonnet 5 and GLM-5.3 from `phase2/list_prices.py`.
- Scripts (in `scripts/`, run in order with `.venv/bin/python`): `extract.py` (existing, builds `runs.json`),
  `p1_build.py` (per-run phases and cost -> `study.json`), `p2_patterns.py` (Q1-Q3a), `p3_handoff.py` (Q3b),
  `p4_tasks.py` (Q4 and re-orientation cost).
- **Counterfactual proxy.** The verifier only runs at the end, so no run tells us whether the builder's work
  would have passed before a revise or takeover. The proxy is the `dt1` GLM-alone run of the same task:
  **one run per task**. A "rescue (proxy)" = a run solved on a task where that GLM-alone run failed. Single runs
  flip (OVERNIGHT_LOOP 22:00), so every rescue count is soft.

## Q1. Plan shape: the planner never split a task
- All 127 traced dev runs have a **1-step plan**; `max_plan_steps` = 1 in every trace; **0 planner LLM calls**.
  The `BASE` caps in `phase2/setups.py` force `max_plan_steps: "1"` for every dev setup (glm, opus, glmreview*,
  takeover*), and the single step's goal is the task instruction verbatim. (`p2_patterns.py`, Q1)
- So there is **no dev evidence on decomposition**: no multi-step runs, no solve rate or cost by plan size, no
  multi-step vs single-step comparison. Multi-step setups (`routed`, `split`, `planner`, `levers`) exist only
  under `cmp`, `cmpd`, `ev`..`ev4` (job names listed, not opened; outside this study's allowed prefixes).
- Closest dev analogues of "plan before work" (one Opus call before GLM starts):
  - **v2.7 strategist** (mode, deliverables, targets; $0.01-0.03 per run, 20 runs). dt1 v2.7 vs GLM alone on 15
    shared tasks: **3 vs 3 solves**, $3.72 vs $1.50. Differences: fin-saccr won, mvcc lost. dv7 v2.7 vs dv6
    v2.6 (no strategist) on 5 shared tasks: **1 vs 3**, $1.06 vs $1.04 (payments and telecom lost). v2.7 also adds
    the 85% checkpoint, so the strategist is not isolated.
  - **v3.1 spec card + builder handoff note** vs v2.8 on 7 shared tasks: **2 vs 4 solves**, $7.14 vs $4.70; vs
    v3.5: 2 vs 4, $7.14 vs $2.50. v3.1 lost embedding, html-js, react; won mvcc.
  - n = 5-15 tasks, one run per cell: small. No sign that up-front planning helped; two of three comparisons
    lean negative.

## Q2. Revise and fix loops (`p2_patterns.py` Q2a-Q2e)
### In-loop revise (v2.6/v2.7): Opus reviews GLM's submit, problems go back into the same GLM session
- 25 valid reviewed runs. Verdict "fix" 19 runs, **5 solved**; "ship" 5 runs, 2 solved; review error 1.
- Added cost is small: on fix runs the review is $2.33 and GLM's revise turns $0.40 (median 8 more commands),
  about **$0.14 per run** on top of GLM's ~$0.09.
- Against the dt1 GLM-alone run of the same task (fix runs): GLM failed, revise failed **12**; GLM solved, revise
  solved **3** (embedding x2, risk-scorer: likely not rescues); GLM failed, revise solved **1** (dv6 payments);
  no GLM baseline 3 (telecom solved once).
- **No case of revise breaking a task GLM solved** among the 19 fix runs. The one v2.7 loss on a GLM-solved task
  (mvcc 0.73) was a "ship" verdict, not a revise.
- Two reviews were triggered by the 85% checkpoint (heat-pump, pretrain-shard); both failed.
- By task: the revise loop solved nothing new on build or answer tasks (0/7 on GLM-failed tasks); its only
  proxy rescue is a service task (payments, 1/3).

### Builder-fix-first (v2.9): a short problem list goes back to a new GLM session once, then re-judge
- Fired in **2 of 10** valid v2.9 runs (fin-saccr, session-window). Each GLM fix cost ~$0.02 (12 and 7 commands).
- **The re-judge never ran.** In both runs the round-2 review failed with `ProviderUnavailable: ... Prompt file
  is empty` (trace `senior_review` events), and the code's fallback (`verdict = "fix"` on exception,
  `phase2/agent.py` `judge_now`) sent both straight to the Opus takeover. Outcomes: fin-saccr solved, session
  failed (0.86). So v2.9's builder-fix-first was **never actually tested on dev**; its "drop" in the overnight log
  measured an extra GLM pass plus a crashed judge call, not the idea. (Source inspected + trace.)

### Judge "fix" then takeover (v2.8-v3.7): Opus judges once, another model takes over in a new session
- Valid takeover-setup runs: judge shipped 18 (7 solved, $3.75); Opus takeover 34 (**15 solved**, $36.76, of
  which takeover $28.20, judge $5.85); Sonnet 5 takeover 3 (1 solved, $2.45); GLM-5.3 takeover 6 (1 solved,
  $4.97).
- Opus takeover by proxy: on tasks GLM alone failed, **11 of 28** takeovers solved, judge+takeover $24.93
  (**$2.27 per solve**). On tasks GLM alone solved, 4 of 6 solved for $9.12 (risk-scorer x3 alone is most of it;
  inference: largely unneeded spend).
- All takeover models, by proxy: (GLM failed, takeover solved) 13 runs; (GLM failed, failed) 23 runs, $22.01;
  (GLM solved, takeover failed) 3 runs. Ship runs: 3 of 9 ships on GLM-solved tasks failed (judge shipped
  partial work).
- Where it rescued (Q2d): payments 4/4, html-js 3/5, fin-saccr 2/2, react 2/4. Where it never did: cargo 0/2,
  heat-pump 0/4 ($1.77 per takeover), kv 0/2, session 0/3, vf2 0/2. Opus alone also fails all five of those.
- html-js and react: the takeover solved tasks that **both** GLM alone and Opus alone failed (n=1 single-model
  run each; Opus alone quit html-js after 3 commands, $0.11). The only dev sign of the combination beating
  either model alone. Small.

## Q3. Handoffs
### Solves by path (`p2_patterns.py` Q3a; 39 solves in 116 valid runs)
| path | solved | $ | $/solve |
|---|---|---|---|
| one session, one model (GLM or Opus alone) | 8/30 | 16.49 | 2.06 |
| one session + in-loop revise (same session) | 5/19 | 4.70 | 0.94 |
| one session, review shipped or none | 2/6 | 1.13 | 0.56 |
| one session, takeover judge shipped / no judge | 7/18 | 3.75 | 0.54 |
| **handoff: GLM -> new session of another model** | **16/41** | 42.89 | 2.68 |
| GLM -> new GLM fix session -> takeover | 1/2 | 1.29 | 1.29 |

17 of 39 solves came after a cross-session handoff, 22 within one continuous session. Handoff runs cost the most
per solve, but on dev they are also where the GLM-failed tasks get solved.

### Information lost at the handoff (`p3_handoff.py`, `p4_tasks.py`; write = heuristic regex on commands)
- Commands before the first write to task files (re-orientation), median: Opus takeover **3** (mean 6.4, max
  34, n=30); Opus alone from scratch **3** (mean 4.2); GLM alone 3; GLM fix session 4; **in-loop revise in the
  same session 0** (mean 1.6, n=19). The takeover re-orients like a cold start despite the builder digest.
- v3.1's handoff note + spec card did not shorten it: median 3.5 (n=4).
- 61% of the takeover's pre-write commands (185/302) re-read paths the builder already touched; 28 of 43
  takeovers do it.
- Opus spend before the takeover's first write: **$8.87 of $28.20** takeover spend over 34 Opus takeovers
  (median 24% per takeover; approximation: call i = command i, and the cold first call is in it).
- Undoing earlier work is rare: 13 of 43 takeovers fully rewrote a file the builder wrote (5 solved, e.g.
  html-js and fin-saccr, where the rewrite was the fix); 1 of 43 reverted to the original snapshot
  (dv9 session-window, failed).
- No multi-step plans exist, so step-to-step loss inside a plan cannot be measured on dev.

## Q4. Task features (`p4_tasks.py`; type labels are this study's reading of the instructions, inference)
| task | what it asks | type | deliverables* |
|---|---|---|---|
| cargo-flight-dispatch | fix fuel, weight and crosswind bugs across 3 Python files; write a flight plan JSON | repair | 4 |
| embedding-drift-monitor | fix KS/PSI/MMD stats and alert debouncer in a drift monitor | repair | 1 |
| session-window-debug | fix session merge, late-event and GC bugs in a stream processor | repair | 2 |
| react-lead-form | fix a React/TS lead pipeline: validation, timestamps, ledger, CRM export, 5 output files | repair | 7 |
| mvcc-lsm-compaction | find a C++ MVCC visibility bug, fix it, add a regression test | repair | 3 |
| pretrain-shard-corruption | find why val loss regressed; restore the original training data | repair | 4 |
| risk-scorer-replay | repair an evaluator against a black-box scorer; 3 outputs | repair | 5 |
| data-anonymization | build a deterministic, memory-capped CSV anonymizer CLI | build | 2 |
| html-js-filter | build an XSS filter that keeps the HTML otherwise unchanged | build | 1 |
| interleaved-vigenere | build a cracker for an unknown classical cipher | build | 2 |
| vf2-speedup-networkx | build a fast NetworkX subset with VF2++ isomorphism | build | 6 |
| kv-live-surgery | speed up a live KV server 5x without dropping connections | service | 3 |
| payments-pipeline-fix | make worker startup fast and notifications exactly-once during respawns | service | 3 |
| fin-saccr-rwa | compute SA-CCR exposure, RWA and capital for two netting sets | answer | 2 |
| heat-pump-warranty | decide every open warranty claim and submit via a portal API | answer/operate | 2 |
| legacy-utility-triage | resolve billing cases in a GUI workstation | operate | 1 |
| telecom-entity-resolution | cluster ~93k records across 4 systems at high precision/recall | answer | 1 |

*Deliverables = the v2.7 strategist's list length for the task, one run each.

Solves on tasks the dt1 GLM-alone run failed (rescue proxy):
| group | Opus takeover | in-loop revise |
|---|---|---|
| service (2 tasks) | 4/6 | 1/3 |
| build (4) | 3/7 | 0/3 |
| answer/operate (4) | 2/6 | 0/2 |
| repair (7) | 2/9 | 0/5 |
| 1-2 deliverables (9) | 5/14 | 0/6 |
| 3+ deliverables (8) | 6/14 | 1/7 |

- Deliverable count does not separate anything (5/14 vs 6/14).
- Takeover rescues concentrate on tasks with one hard core fault that a strong model can find from the code
  (payments startup, XSS edge cases, SA-CCR rules) (inference). It never rescued cargo (0.70 plateau), session (0.57), vf2 (0.98),
  heat-pump (0) or kv: 0 of 13 takeovers, and Opus alone fails all five too. That fits `our_dev_traces.md`
  section 6: shared misreadings, not missing model strength (inference). The exception is html-js, a 0.50
  plateau for every single model, which the takeover broke 3 times in 5.
- Repair tasks with several symptoms are where GLM alone already does best (3/7). Revise and takeover add the
  least there.

## Q5. Verdicts
| pattern | verdict | evidence, sample size |
|---|---|---|
| **Plan (split into steps)** | **No evidence** | 0 of 127 dev runs had more than 1 step; `max_plan_steps` forced to 1. Up-front planning analogues (strategist, spec card) lean negative: 3 vs 3, 1 vs 3, 2 vs 4 solves on 5-15 tasks, one run each. Small. |
| **Execute (one continuous session)** | **Helps on cost, baseline on solves** | 8/30 alone; 7/18 judge-shipped at $0.54/solve. Cheapest per solve wherever the builder can finish. |
| **Revise (in-loop review, same session)** | **No measurable help; no harm seen** | 1 proxy rescue in 19 fix runs (plus 1 with no baseline); 0 cases of breaking GLM-solved work; +$0.14/run. n=19: small. |
| **Fix by the builder (v2.9, new GLM session)** | **No evidence (untested)** | Fired 2 times; the re-judge crashed both times (empty prompt file), so both went to takeover. n=2. |
| **Fix by takeover (judge fix -> Opus)** | **Helps solves, costs most** | 11 of 28 Opus takeovers solved on GLM-failed tasks, $2.27 per solve for judge+takeover; ~24% of takeover spend is cold re-orientation; 0/13 on cargo, heat-pump, kv, session, vf2. n=34 Opus takeovers on 12 tasks, one GLM baseline run per task: moderate at best. |

## What's next
Fix the v2.9 round-2 judge bug (`ProviderUnavailable: Prompt file is empty` on the resumed review session in
`phase2/agent.py` `judge_now`), then rerun v2.9 builder-fix-first on fin-saccr, session-window, html-js and
payments only (the tasks where the judge said fix). It is the one fix pattern our data never actually measured.
Do not rerun decomposition on dev until a setup with `max_plan_steps` > 1 is in scope.

## Change Record
Task: Plan/execute/revise/fix study on dev runs
Linear: None
Update: Dev runs never split a task (max_plan_steps 1); in-loop revise shows no measurable gain; v2.9 builder-fix-first was never tested (re-judge crashed both times); Opus takeover solved 11/28 GLM-failed task runs at $2.27 per solve.
Evidence: Verified locally (scripts p1-p4 on jobs/, 2026-11-18)
Next: Fix the v2.9 round-2 judge crash and rerun builder-fix-first on its 4 fix tasks.
Edited by: Claude Code (Anthropic, Claude Opus 5.5) — 2026-11-18T18:45:19+04:00
