# v4 dev results (batch v4d6, 2026-11-18/19)

Setup ocxv4 (v2.8 + A1 verified done + judge out-of-turns = fix + tool probe) on the opencodex backend,
12 dev tasks x 2, paused at 06:05 Dubai (Ibrahim leaving). Compared with dv8 (v2.8) and dv13 (v3.5), both
Droid-era, one run per task. Costs are tokens x list price. Script: scripts/v4_assess.py. Evidence: Verified locally.

## Verdict: v4 is not better than v2.8. Do not freeze it as is.

| | runs | solve rate | cost/run | takeover rate | cost/solve |
|---|---|---|---|---|---|
| v4 (v4d6) | 21 valid | 0.43 | $1.07 | 0.86 | $2.51 |
| v2.8 (dv8) | 12 | 0.50 | $0.92 | 0.92 | $1.85 |
| v3.5 (dv13) | 12 | 0.50 | $0.69 | 0.83 | $1.37 |

- A1 keep rule (takeovers down >=25% at solves within 1) fails: takeovers -7%, solve rate -0.07.
- Lost solves on html-js-filter (0/2 vs v2.8 1, v3.5 1) and react-lead-form (1/2). Opus takeovers in v4 ran
  8-9 commands vs 4-5 under v2.8 and spent part of them extending /tmp/phase2_checks.sh.
- The judge overrode green builder checks 8 of 11 times; 4 of those 8 then solved, 1 of 3 shipped solved.
  So the checks do not tell the judge when the build is right: A1's premise does not hold.
- Confound: v4 runs on opencodex, v2.8 ran on Droid. GLM through opencodex streams ~59 tok/s vs ~135, needed
  effort pinned to high (API default max), a longer builder wall (3300 s, which made kv-live's live load
  generator expire before the takeover), and had empty replies from broken streams (fixed d5aea2b).
  Part of the gap may be backend, not policy.

## Per task (v4 r1, r2)
```
cargo-flight-dispatch     v2.8 0 $0.53 | v3.5 0 $0.30 | v4 0 $0.21 tk0 ship chk95 0 $0.27 tk0 ship chk31
embedding-drift-monitor   v2.8 1 $0.13 | v3.5 1 $0.33 | v4 1 $0.26 tk1 fix chk28 1 $1.03 tk1 fix chk40
fin-saccr-rwa             v2.8 1 $0.49 | v3.5 0 $0.26 | v4 1 $0.71 tk0 ship chk49 1 $1.35 tk1 fix chk15
heat-pump-warranty        v2.8 0 $2.25 | v3.5 0 $0.39 | v4 0 $1.40 tk1 fix chk0 0 $1.53 tk1 fix chk0
html-js-filter            v2.8 1 $0.45 | v3.5 1 $0.43 | v4 0 $0.53 tk1 fix chk29 0 $0.56 tk1 fix chk22
kv-live-surgery           v2.8 0 $0.90 | v3.5 0 $0.70 | v4 0 $1.53 tk1 fix chk0 0 $0.25 tk1 fix chk0
mvcc-lsm-compaction       v2.8 0 $0.36 | v3.5 1 $0.12 | v4 1 $0.35 tk1 fix chk16 0 $0.21 tk1 fix chk10
payments-pipeline-fix     v2.8 1 $1.41 | v3.5 1 $0.82 | v4 INFRA 1 $4.36 tk1 fix chk0
react-lead-form           v2.8 1 $0.49 | v3.5 1 $0.67 | v4 1 $1.92 tk1 fix chk0 0 $1.25 tk1 fix chk0
risk-scorer-replay        v2.8 1 $2.59 | v3.5 1 $1.80 | v4 1 $1.38 tk1 fix chk0 1 $1.15 tk1 fix chk0
session-window-debug      v2.8 0 $0.42 | v3.5 0 $0.37 | v4 0 $0.71 tk1 fix chk18 -
vf2-speedup-networkx      v2.8 0 $1.08 | v3.5 0 $2.04 | v4 0 $1.61 tk1 fix chk0 -
```
Not run (pause): vf2 r2, session-window r2, payments r1 rerun (broken stream) (risk-scorer r2 finished before the hard stop and solved).

## Options (Ibrahim to decide)
1. Freeze v2.8 as the final policy (held-out 11/36, $3.80/solve already measured) and start writing; report
   v4/A1 as a tested-and-rejected idea with this table. Cheapest, honest.
2. Separate backend from policy first: run ocx28 (v2.8 on opencodex) on the same 12 tasks x 1 (~5 h GLM time,
   ~$11). If ocx28 also drops, the backend is the cause and v4's ideas deserve one more try.
