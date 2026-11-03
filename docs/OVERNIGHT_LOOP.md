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
- 22:00 red team on v3.4 (low-effort takeover): build with guards. 836/905 replies are cut at 300 chars in traces, so
  "output = reasoning" is unproven; long calls (>2000 tok, likely heredoc file writes) carry 53% of takeover output;
  solves with long calls (hof, atrx, payments, nextjs) are at risk; a win from an effort knob is drift (tunes the
  strong model, not routing) and would need Opus-alone-at-low for fairness. => 2-task probe only (dv12: payments,
  html-js); keep rule: takeover solves kept, output/call -40%, calls +<=20%, Opus cost -20%.
  Next on-thesis cost lever (WHAT THEY KNOW): per-role context - the takeover gets compacted observations and the
  handoff instead of full 10k-char outputs (fresh input is 40% of Opus spend).
- 22:30 dev, same tasks vs v2.8: v2.9 5/10 $10.58 (v2.8 6/10 $9.88) DROP; v3.0 GLM-5.3 takeover 1/7 $3.16 (v2.8 4/7
  $5.15) DROP, stopped; v3.4 low effort: html-js takeover quit after 2 calls, fail (v2.8 solved) DROP, stopped;
  v3.1 2/6 $5.28 (v2.8 3/6 $4.62) still running. Finding: 73 of 934 takeover calls broke the prompt cache and cost
  $6.52 (29% of takeover fresh input); 58 of them ($4.30) were our own session restarts (session_budget_chars 80k).
  Theory: with caching, a long cached context costs 1/20 of re-sent input, so compaction raises cost.
  => v3.5 = v2.8 + session_budget_chars 300k, dev dv13. Also: takeovers cost ~$1.15 vs an Opus-alone run $0.89,
  because Opus alone often gives up in 1-3 min; v2.8's edge is solves, so cost must come from waste, not effort.

## State for a fresh context (22:50)
- Best version: v2.8 (takeover28). Held-out 11/35 $40.23 vs Opus alone 4/35 $31.31 (reports/heldout-v28.html).
- Running: dv11 (v3.1 context), dv13 (v3.5 cache-keeping). Dropped: v2.9, v3.0, v3.3 (not run), v3.4.
- Next: when dv13 ends compare with dv8 on the same tasks; if v3.5 keeps solves and is cheaper, confirm on held-out
  ho5+ho4 (setup takeover35, same seeds). Then v3.6 = ledger (ideas doc section 12), red-team first.
- Crons in this session: health check :07/:37, improvement loop :22/:52; Phase 1 resumes 23:50 (Pro window).
- Red-team agent: .claude/agents/harness-red-team.md (subagent_type harness-red-team).
- 22:50 Droid hit its 5-hour usage limit ("You've reached your 5-hour Droid Core usage limit", resets ~23:47; read from
  ~/.factory/logs). Droid usage is now the scarce resource before expiry. Priority: v3.5 dev (dv13, cheap,
  evidence-backed) -> held-out confirmation of the best version. Stopped dv11 (v3.1: 2/6 vs v2.8 3/6, costlier).
- 00:25 v3.5 dev so far 4/9 $3.31 vs v2.8 4/9 $6.57 on the same tasks (3 running: payments, risk-scorer, vf2). jobs/ho_v35.chain.sh confirms on held-out ho5+ho4 automatically if v3.5 ends with solves >= v2.8 at lower cost.
- 01:49 v3.5 dev final: dv13 6/12 $8.78 vs v2.8 6 $12.00 GO. KEPT. Held-out ho5+ho4 (24 runs, takeover35) started 01:48.
- 02:20 Phase 1 Claude Code column DONE: 40/40, 14 passed, $32.49 list price.
- 02:20 v3.5 held-out interim: 0/14 solved ($6.6) vs v2.8 on the same sets (ho5 6/12, ho4 1/11). Takeovers are half
  as long: median 9 calls vs 18.5 in v2.8 (8 vs 18 takeovers). Hypothesis: v2.8's session restart at 80k chars re-sent
  a compacted view that restated the task, which acted as a "keep going" nudge; without it Opus submits early.
  Cheaper, but it looks like it loses solves. Candidate v3.6: keep the cache (300k) AND restate the goal + open
  problems periodically by APPENDING a reminder (cache-safe) instead of restarting the session.
- 02:35 CORRECTION (red team): the "v3.5 0/14 held-out" interim compared finished runs only; 5 of v3.5's 10 unfinished
  trials are exactly the tasks v2.8 solved on ho5 (mp r1/r2, production r2, sglang r2, sound-change r1). On the same
  14 finished trials v2.8 solved 1 (nextjs r2) vs v3.5 0: noise. The median-9-calls figure has the same bias (the
  unfinished takeovers were at 25, 25, 13, 11...). Restart-as-nudge is not supported: 5/11 solved v2.8 takeovers and
  10/19 failed ones restarted. v3.6 DROPPED and dv14 stopped. Lesson: never read an interim held-out score; compare
  only the same finished trials, and wait for the slow ones (solves are often the long runs).
- 03:20 v3.5 held-out FINAL (ho4+ho5, 24 runs, same seeds): v3.5 5/24 $22.22; v2.8 7/24 $25.67; Opus alone 4/23
  $21.10 (~$22.0 per 24 runs at its rate). v3.5 beats Opus on solves, ties it on cost: goal NOT met. Versus v2.8 it
  is 13% cheaper and 2 solves fewer (ctr r1 / production r2 lost; noise on 24). Takeover is 85-90% of every run's
  cost (run_notes), so the next lever is who does the takeover, not more context. Best version stays v2.8 for solves;
  v3.5 is the cheaper variant. Sonnet 5.5 is gone from Droid (catalog 03:20 lists claude-sonnet-5, not 5.5).
- 03:23 v3.7 (takeover37) = v3.5 with claude-sonnet-5 doing the takeover (list $2/$10, half of Opus; Opus still plans
  and judges). Red team: build with guards; finishing v3.5 on ho1/ho2 cannot win on cost (~$36 vs Opus $31.31 over 36).
  Its v3.0 precedent (GLM-5.3 takeover 1/7 vs 4/7) is the risk. Dev dv15 started (parallel 6). Keep rule (chain
  jobs/ho_v37.chain.sh): >=5/12, cost <= 75% of dv13 on the same tasks, at most 1 dv13 solve lost -> then held-out
  ho5+ho4 takeover37, which must beat Opus 4/23 on solves and come in under ~$17.
- 03:55 v3.7 DROPPED (red-team rule: 2 solves behind on the same finished trials). Same 7 dev tasks: v3.7 2/7 $3.36 vs
  v3.5 4/7 $2.70. Sonnet 5 takeovers ran longer and cost more than Opus's: heat-pump 51 calls $1.29 (Opus 5, $0.19),
  html-js-filter 15 calls $0.26 and failed (Opus 4 calls, solved). Half the price per token did not buy half the cost:
  the strong model finishes in fewer calls. Same lesson as v3.0 (GLM-5.3 takeover). dv15 and its chain stopped with
  5 runs unfinished to save Droid. Takeover stays Opus; cost must come from fewer/shorter takeovers, not a weaker one.
- 04:10 v3.8 (takeover38: v3.5 + Opus takeover capped at 20 commands) built, DROPPED before any run (red team).
  Offline sim (prices each traced takeover call; a solve counts only if it needed <= N calls): v2.8 held-out cap 20
  6/36 $31.76, cap 15 5 $27.44; but on v3.5 ho4+ho5 cap 20 gives 2/24 $16.38 (below Opus 4/23) and cap 35 keeps 5
  for only $0.45 less. The long takeovers ARE the solves (mp 33/35, sglang 28, vpp 47, sglang 59). N would also be
  chosen on held-out traces (dev has one solve past 20 calls), and the takeover is not told its budget. Dollar caps
  are worse. Lesson: cost must come from FEWER takeovers (the judge handing over less often), not shorter ones.
- 04:12 v3.5 on ho1/ho2 started (prefix ho2, 6 tasks x2, same seed 570): the only held-out sets v3.5 has not been
  looked at on, to give v3.5 a full 36-run record next to v2.8 and Opus (record, not a tuning step).
- 04:35 "Fewer takeovers" tested OFFLINE first (scripts/replay_rescue.py, $0.30): on 27 past dev handovers (dv8, dv11,
  dv12, dv13) Opus was asked the odds that a 60-command takeover passes, from what the judge knew at handover. It does
  not separate: solves got P=0.12-0.72, failures P=0.03-0.60. Only P<0.1 is clean (2 runs, both kv-live-surgery, one
  task, $1.23). Skip P<0.3 loses 1 of 11 dev solves for $5.27; P<0.4 loses 7. Not built: the judge cannot foresee a
  rescue, same as its problem count (red team, v3.8). Builder exit is no better (TimeExceeded builds gave 3 held-out
  solves). Output: jobs/replay_rescue.dev.txt. Open lever left: none evidence-backed for cost tonight.
