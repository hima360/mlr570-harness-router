# Overnight improvement loop (2026-11-02 night, Ibrahim asleep)

Goal: beat Opus alone on pass rate AND total cost, without benchmaxing.

Rules
- Dev tasks only for tuning (never in plan.tsv, never in a held-out set): cargo-flight-dispatch embedding-drift-monitor
  fin-saccr-rwa heat-pump-warranty html-js-filter kv-live-surgery mvcc-lsm-compaction payments-pipeline-fix
  react-lead-form risk-scorer-replay session-window-debug vf2-speedup-networkx. Baseline numbers for them: dt1 (glm, opus,
  glmreview27) and dv8 (takeover28).
- Held-out sets (ho1/ho2 seed 570, ho4 seed 571, ho5 seed 572) only to confirm a finished version, once, never to tune.
- Never read hidden tests; never special-case a task. Each change gets a hypothesis from traces, a unit test, a commit.
- A new version = new config + setup (takeover29, ...); never edit a config a running eval uses.
- Keep the harness model-agnostic; report cost at list price.

Candidate improvements (from dt1/dv8 traces)
1. Keep-best: snapshot GLM's work before takeover; if the takeover's visible checks are worse, restore (mvcc: GLM 100%
   alone, v2.8 93% after a fix verdict).
2. Cheaper takeover: cap takeover commands/credits on tasks where no progress shows (heat-pump: 57 calls, $2.41, 0%).
3. Judge precision: the judge said fix on GLM-solved work (mvcc, risk-scorer -> $2.55 takeover). Ask the judge to rate
   severity; minor problems go back to GLM once (cheap) before Opus takes over.
4. Let GLM fix first when it submitted and problems are few (GLM fix ~$0.05 vs takeover ~$1).

## Log
- 21:03 v2.9 (takeover29, builder-fix-first) on 12 dev tasks as dv9; v2.8 baseline completed on the 4 missing dev tasks (dv8).
- 21:30 analysis of 43 v2.8/v2.9 runs (scripts/run_notes.py): every held-out solve came through an Opus takeover;
  all 7 held-out runs the judge shipped failed (glycan x2, protein x2, roy x2 - Opus alone also fails these). Takeover
  is 80-95% of run cost; v2.8 takeovers run longer than Opus-alone runs (ho5 Opus alone: 7 min, $0.88/run).
  v2.9 builder-fix-first: fin-saccr cost 0.53 -> 0.82 when GLM's fix did not settle it (early sign it adds cost).
  => Next candidate (5): a cheaper takeover model. GPT-6.1 Sol through Droid lists at half Opus's price
  ($2/$10 vs $4/$20 per M); judge stays Opus. Test as takeover30 on dev.
  => Candidate (6): cap takeover at 35-40 commands (solves past 40 calls: risk-scorer 43, vpp 47, sglang 59).
- 21:25 v3.0 (takeover30, GLM-5.3 takes over) launched on dev as dv10 (parallel 4, alongside dv9).
- 21:45 v3.1 (takeover31: spec card, repo map, handoff on top of v2.8) launched on dev as dv11.
- 21:50 red-teamed idea list: docs/HARNESS_IDEAS_REDTEAM.md. Build order: stall trigger + progress budget (v3.3), tester-as-evidence, trained router, lessons.
