# Harness ideas, red-teamed (2026-11-02)

Each idea: what it is, the theory behind it, what our own traces say, the red-team case against it, and the
concrete guard that answers the red team. "Build" = the version and the measurable test that keeps or drops it.
Evidence from our runs: dt1 (114 runs), dv8/dv9 (v2.8/v2.9 dev), ho5 v2.8 (12 held-out runs), scripts/run_notes.py.

Literature named here is from memory (Unverified for exact numbers); our own numbers are Verified locally.

## 1. Lesson memory across tasks
- Theory: Reflexion (Shinn et al. 2023) and ExpeL-style episodic memory: verbal lessons from past attempts improve
  later attempts without weight updates.
- Our data: failures repeat in kind (builders end without checking the way tests run; takeovers re-explore).
- Red team: (a) lessons over-fit to the dev tasks: that is benchmaxing by another name; (b) wrong lessons spread a
  mistake to every task; (c) retrieval by similarity pulls an irrelevant lesson that misleads; (d) on 12 dev tasks
  any gain is inside run-to-run noise.
- Guard: lessons must be general (no file names, task names or numbers; a filter rejects them), at most 3 per task,
  written only from runs whose outcome is known, frozen before held-out, and shown as "hints, may not apply".
  Kept only if held-out improves, never judged on dev alone.

## 2. Live shared notebook (/tmp/NOTES.md)
- Theory: blackboard architectures (Hearsay-II); shared scratchpads in multi-agent LLM systems.
- Red team: notes go stale or wrong and later roles trust them over the code; long notes cost tokens on every turn
  for every role (Opus input is the bill); a role that edits notes instead of code wastes calls.
- Guard: notes are capped (2 KB), each line tagged with who wrote it and whether it was checked; the takeover is told
  "verify before trusting". Measured by takeover calls before its first edit.

## 3. Tester role (checks written from the spec card, used by the judge)
- Theory: CodeT (Chen et al. 2022) and AgentCoder: generated tests select and repair code; evidence beats opinion
  for LLM judges, which show position and verbosity bias (Zheng et al. 2023, LLM-as-a-judge).
- Our data: every held-out run the judge shipped failed (7 of 7); the judge said "fix" on 23 of 29 v2.7 failures.
- Red team: (a) tests written from the task text are wrong as often as the code (2026-10-29: our own acceptance
  tests passed every near-miss); (b) a wrong test turns a correct build into a forced takeover (cost) or "fixes" it
  into a failure; (c) extra role = extra cost on every task.
- Guard: tester checks are evidence for the judge, never an automatic verdict; the judge sees which checks fail and
  decides. Only the checks the judge confirms count. Cheap model, 15 commands. Kept only if ship precision rises.

## 4. Scout before takeover
- Theory: the agent-computer interface matters (SWE-agent, Yang et al. 2024): agents waste turns finding their way.
- Our data: Opus takeovers run 5-59 calls; the long ones re-read the repo.
- Red team: the handoff note (v3.1) may already do this; a scout summary from a weak model can be wrong and the
  strong model inherits the error; one more hop adds latency.
- Guard: build only if v3.1's handoff does not cut takeover calls; the scout reports facts with the command that
  shows each (paths, failing test output), never opinions.

## 5. Breaker (adversarial edge-case tester)
- Theory: red-teaming / property-based testing finds edge-case bugs specs imply.
- Red team: breakers invent edge cases the hidden tests do not check, causing needless takeovers and over-
  engineered code that breaks the simple case (the v2.7 failure mode: review-requested changes broke correct work).
- Guard: only edge cases named in the spec card; results go to the judge as evidence like (3). Lowest priority.

## 6. Stall trigger (early handover)
- Theory: cascades should escalate as soon as the cheap model's failure is evident (FrugalGPT, Chen et al. 2023).
- Our data: builders that hit TimeExceeded/LimitsExceeded burned 25 min first (kv-live-surgery, hof, intrastat).
- Red team: "no file changed in 8 commands" also describes healthy reading and testing; early handover cuts off
  builds that were about to work and raises Opus spend.
- Guard: stall = no file change AND repeated error text (same error twice) within 8 commands, or 3 identical
  commands. Measured on dev: takeover count must not rise more than solves.

## 7. Progress budget for the takeover
- Theory: anytime algorithms / budgeted search: spend more only while marginal progress is positive.
- Our data: heat-pump-warranty takeover: 57 calls, $2.41, 0% tests; Opus alone there: $2.61, 0%.
- Red team: progress is hard to see without hidden tests; some solves come late (sglang solved at 59 calls,
  vpp at 47) and a budget cap kills them.
- Guard: grant takeover budget in blocks of 15 commands; a block is renewed when it changed files AND a check it
  ran improved or a spec item was confirmed; at most 4 blocks. Kept only if solves on dev do not drop.

## 8. Two cheap builds, judge picks one
- Theory: self-consistency / best-of-n sampling raises pass rates; cheap samples before expensive ones.
- Red team: GLM-Flash fails the same way twice (low diversity), doubling builder cost for nothing; the judge picks
  by opinion again (see 3).
- Guard: the second build gets a different work mode (the v2.7 strategy modes); only with tester evidence (3).

## 9. Keep-best (snapshot and roll back)
- Theory: monotone improvement with a verifier; never accept a step that lowers the measured score.
- Our data: mvcc-lsm-compaction: GLM alone 100%; v2.8 after a fix verdict 93%.
- Red team: without hidden tests "worse" is measured on our own checks, which can be wrong (3a); rollback can
  restore a build the judge already found broken.
- Guard: roll back only when the same check set (task-provided tests if any, else tester checks the judge accepted)
  is strictly worse after the takeover.

## 10. Cache-friendly prompts
- Theory: provider prompt caching bills repeated prefixes at the cache-read rate (Opus: $0.20 vs $4 per M input).
- Red team: Droid's CLI may already cache (traces show large cache_read counts), so gain may be near zero; reordering
  prompts can change behaviour.
- Guard: measure cache-read share per role from the traces first; build only if the takeover's share is low.

## 11. Trained router (v3.2)
- Theory: RouteLLM / FrugalGPT: a learned router sends easy queries to cheap models; our plan: Laya embeddings + a
  small head, Arch-Router when unsure.
- Red team: ~20 labelled tasks is tiny; labels from one run each are noisy (TB tasks vary run to run); a router that
  sends a solvable task to the "hopeless, cheap only" path loses a solve to save money; Laya scores collapsed to the
  middle option before (CLAUDE.md).
- Guard: 6-fold cross-validation reported with its spread; the "hopeless" route still gets the judge and a capped
  takeover (7) instead of none; compare against the judge-only cascade on the same dev tasks.

## Order for tonight
6 (stall trigger) and 7 (progress budget): cost, low risk. Then 3 (tester as evidence), then 11 (router), then 1.
Each as its own version on the 12 dev tasks; kept by the rule in OVERNIGHT_LOOP.md.
