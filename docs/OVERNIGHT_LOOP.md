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
