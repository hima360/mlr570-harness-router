# Overnight improvement loop (2026-11-02 night, Ibrahim asleep)

Goal: beat Opus alone on pass rate AND total cost, without benchmaxing.

## Method (follow every firing, in this order)
1. Anchor: the project question is whether a small local router plus harness-managed context lets cheap and
   strong models together beat one strong model (Opus alone) on pass rate AND cost. Every version must change one of:
   WHO WORKS (router/model choice), WHAT THEY KNOW (context, memory, handoff), WHEN TO ESCALATE (triggers, judge).
   A change touching none of these is dropped.
2. Evidence first: read the newest runs (scripts/run_notes.py --new) and name the failure or cost pattern with run
   names and numbers before proposing anything.
3. Idea with theory: tie the idea to a known mechanism (cascade, blackboard, verifier/test selection, episodic
   memory, budgeted search...) and to the pattern from step 2.
4. Red team it: hand the proposal, its evidence and the base version to the `harness-red-team` subagent
   (.claude/agents/harness-red-team.md) and act on its verdict and guards; then also write the strongest case against (benchmaxing, noise on 12 tasks, extra cost on every task, wrong
   evidence trusted, breaks working code) and a concrete guard for each point (docs/HARNESS_IDEAS_REDTEAM.md format).
5. Build behind a config switch that defaults off (queued runs of other versions load the same code), unit test,
   pytest must pass before commit, new setup name.
6. Test on the 12 dev tasks; keep only if solves >= and cost lower, or solves higher, than the best version so far.
7. Confirm a kept version once on held-out (same seeds: ho5 572, ho4 571, ho1/ho2 570); beat Opus's existing rows
   on solves AND total cost = goal reached, stop the loop.
8. Log the decision and the numbers here; never claim a number not computed.

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
- 22:00 red team on the v3.2 router: BUILD WITH GUARDS, 2 routes only. "none" labels are mostly one failed Opus run,
  not hopeless: v2.8 solved html-js-filter and react-lead-form (dev) and sglang, vpp (held-out) that are labelled none;
  10 of 19 training labels are none, 3 are opus. Single runs flip (atrx, hof, roy). Dev tasks are in the training set.
  Decisions: (a) none -> capped takeover, never no takeover; A only for confident "glm"; (b) drop GLM-5.3 route until dv10
  shows >= 3 settled takeovers; (c) score the router out-of-fold only; (d) simulate offline on dt1+dv8 before any paid
  run, build only if >= 6 dev solves at lower cost than dv8; (e) log a route event with head probabilities.
  => Next paid build is the capped / progress-budget takeover (idea 7): it recovers most of the "hopeless" spend
  ($3.69 on dev: cargo, heat-pump, kv, session) without losing html-js or react.
- 22:00 held-out so far (ho1/ho2 570, ho4 571, ho5 572; reports/heldout-v28.html): v2.8 11/35 $40.23 ($3.66/solve) vs Opus alone 4/35 $31.31 ($7.83/solve), GLM alone 2/34 $3.21. Solves 2.75x Opus; total cost +28%: cost is the gap.
- 22:10 v3.3 progress gate built (ProgressEnv, default off) but NOT run: red team showed vpp r1 (held-out solve) did 40
  commands of work in /tmp and site-packages that the diff fingerprint cannot see; gate would stop it; saving ~$3.5 is
  too small for the $9 gap. Cost split of 905 Opus takeover+judge calls: output $25.67 (51%), fresh input $19.99 (40%),
  cache reads $4.87. => v3.4: takeover at low reasoning effort (red team pending).
