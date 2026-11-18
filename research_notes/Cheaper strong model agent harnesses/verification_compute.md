# Verification and test-time compute allocation between cheap and strong models (coding/terminal agents)

Scope: methods that spend cheap-model compute (repeated samples, self-generated tests, verifiers, critics) to avoid or shorten strong-model work. Research done 2026-11-18. Many sources are 2024-2025 and are flagged as older; frontier models used in them (Claude 3.5 Sonnet, Qwen2.5-Coder-32B, DeepSeek-Coder-V2) are well behind current models. Numbers are as published; "[inferred]" marks my own arithmetic or reasoning.

Project context (from the task brief, not researched): GLM-5.3 Flash once, Opus 5.5 judges and takes over on failure. GLM alone 2/34 held-out; judge "ship" verdict right 0/8; all solves via Opus takeover.

## Q1. Best-of-n / repeated sampling with cheap models plus a verifier vs one strong attempt

### Takeaway
Repeated sampling raises *coverage* (pass@k, oracle) a lot, but on SWE-style tasks real selectors recover only about half of the gap between random pick and oracle, so Best@k sits roughly 10 points under Pass@k. No primary source found gives a clean dollar comparison of "cheap model x n + verifier" against "one strong-model attempt" on an agentic benchmark.

### Cited Findings
- Large Language Monkeys (arXiv 2407.21787, v1 31 Jul 2024, v3 30 Dec 2024; older): DeepSeek-Coder-V2-Instruct on SWE-bench Lite solves 15.9% with 1 sample and 56% with 250 samples, above the then single-sample SOTA of 43%. This is coverage (oracle selection). The paper notes that majority voting and reward models "plateau beyond several hundred samples" in domains without automatic verifiers. — [arXiv 2407.21787](https://arxiv.org/abs/2407.21787)
- CodeMonkeys (arXiv 2501.14723, 24 Jan 2025; older): Claude 3.5 Sonnet (20241022) generates candidates; 10 parallel state machines x 8 serial iterations per instance. Oracle coverage 69.8% on SWE-bench Verified; random selection expectation 45.8%; final after selection 57.4%. Total cost $2,291.90 (~$2,300) for the 500-instance set: edits $1,366.02 (59.6%), test generation $439.99 (19.2%), relevance scan with local Qwen2.5-Coder-32B $334.02 (14.6%), selection $131.95 (5.8%), ranking $19.92 (0.9%). — [CodeMonkeys HTML](https://arxiv.org/html/2501.14723)
- CodeMonkeys: selecting with model-written-test voting followed by a dedicated multi-turn selection trajectory beat pure majority voting; model selection without top-3 test filtering did worse. Selecting among edits from existing top leaderboard submissions reached 66.2%, above the best single ensemble member. — [CodeMonkeys](https://arxiv.org/abs/2501.14723)
- CodeMonkeys appendix: DeepSeek-V3 on a 100-instance subset reached about 86.8% of Claude's score at a much lower cost (no dollar figure in the extract). — [CodeMonkeys HTML](https://arxiv.org/html/2501.14723)
- CodeMonkeys on serial vs parallel: first few serial iterations matter a lot; beyond that, configurations of similar total cost reach similar coverage, but more candidates make selection harder. — [CodeMonkeys HTML](https://arxiv.org/html/2501.14723)
- SWE-Gym (arXiv 2412.21139, Dec 2024, ICML 2025; older): OpenHands agent and verifier both Qwen2.5-Coder-32B on SWE-bench Verified: k=1 20.6%; k=8 Pass@8 37.8% vs Best@8 29.8%; k=16 Pass@16 42.8% vs Best@16 32.0% (+11.4 pts over k=1). SWE-bench Lite (MoatlessTools, k=8): 32B 19.7% -> 26.3%; 7B 10% -> 13.3%. — [SWE-Gym HTML](https://arxiv.org/html/2412.21139)
- Agentless (arXiv 2407.01489, Jul 2024; older): 32.00% (96/300) on SWE-bench Lite at $0.70 average cost, via localisation, repair, and patch validation (sampling plus test-based filtering). — [Agentless](https://arxiv.org/abs/2407.01489)
- "Smaller, Weaker, Yet Better" (arXiv 2408.16737, Aug 2024; math reasoning, not coding agents): at matched compute, samples from a weaker cheaper model have higher coverage and diversity but higher false-positive rates than a stronger model's. — [arXiv 2408.16737](https://arxiv.org/abs/2408.16737)

### Inferences
- [inferred] CodeMonkeys selection recovers (57.4-45.8)/(69.8-45.8) = 48% of the random-to-oracle gap. SWE-Gym loses 10.8 pts from Pass@16 to Best@16. Selection, not generation, is the binding constraint.
- [inferred] Cheap-model sample gains are largest when the cheap model's pass@1 is non-trivial. A 2/34 (5.9%) pass@1 is near the bottom of the curves above; even generous pass@k scaling would leave most tasks for the strong model, and the verifier must then find the rare correct sample among many wrong ones, which is where false-positive rates bite.
- [inferred] Generation, not verification, dominated CodeMonkeys' spend (edits 59.6% vs selection 5.8%). Verification is cheap relative to generation, so the economics hinge on verifier precision, not verifier cost.

### Gaps
- No primary source found that directly prices "cheap model x n + verifier" against "one frontier attempt" on SWE-bench Verified or Terminal-Bench with current (2026) models.
- Large Language Monkeys extract had no per-dollar comparison against GPT-4o/Claude for SWE-bench.

## Q2. Test generation as verifier and execution feedback: does it tell a cheap model it is done?

### Takeaway
Self-written tests help selection but are weak stop signals: test-based verifiers have low distinguishability, and execution-free (LLM-judge) verifiers rely on style. Hybrids beat either alone. Nothing found shows a cheap model reliably knowing it is done.

### Cited Findings
- R2E-Gym (arXiv 2504.07164, 9 Apr 2025; older): 32B agent 34.4% pass@1 on SWE-bench Verified. Execution-based and execution-free verifiers each saturate around 42-43%; hybrid reaches 51%. "Test-based verifiers suffer from low distinguishability"; execution-free verifiers "are biased and often rely on stylistic features." — [R2E-Gym](https://arxiv.org/abs/2504.07164)
- CodeMonkeys generates tests in the same trajectory as edits and votes with them; test generation cost 19.2% of budget; test voting alone was beaten by test filtering + model selection. — [CodeMonkeys HTML](https://arxiv.org/html/2501.14723)
- Practitioner evidence (small samples, not peer reviewed): cheap-model self-confidence gating "fails on exactly the queries that NEED escalation"; a cheap YES/NO code judge was 18/18 on a tiny coding set. — [GitHub llm-model-routing-benchmark](https://github.com/dennisonbertram/llm-model-routing-benchmark/tree/main/03-pocs/L3a-frugalgpt-cascade)
- Large Language Monkeys: coverage turns directly into performance only where an automatic verifier exists (unit tests, proof checkers). — [arXiv 2407.21787](https://arxiv.org/abs/2407.21787)

### Inferences
- [inferred] The project's 0/8 correct "ship" verdicts matches this literature: an LLM reading a cheap model's output is not a reliable done-signal. The task's real verifier (hidden tests) is unavailable at run time; self-generated tests share the builder's misreading of the spec.
- [inferred] A hybrid stop signal (execution of agent-written tests + a separate judge, ship only when both agree) is the published pattern most likely to raise judge precision; it trades recall (more handovers) for precision.

### Gaps
- No source measured the precision of a cheap agent's own "I'm done" claim on Terminal-Bench-style tasks.

## Q3. Learned critics / trajectory verifiers and cross-distribution generalisation

### Takeaway
Trained trajectory critics give real Best@k gains on SWE-bench Verified, and small (4B) critics now compete with frontier judges. Generalisation is the weak point: critics trained on benchmark data score near random on production outcomes; the strongest generalisation claims (Scout) are recent and self-reported.

### Cited Findings
- OpenHands critic (blog, 17 Apr 2025; older): Qwen2.5-Coder-32B critic with regression head, trained by TD learning (reward 1 if all unit tests pass, gamma 0.99). SWE-bench Verified 60.6% single rollout -> 66.4% with 5 attempts. Generalisation beyond SWE-bench stated as not guaranteed, no evidence given. — [OpenHands blog 2025](https://openhands.dev/blog/sota-on-swe-bench-verified-with-inference-time-scaling-and-critic-model)
- OpenHands "Learning to verify" (blog, 5 Mar 2026): ~4B critic (openhands-critic-4b-v1.0) trained on production traces. Mixed-outcome SWE-bench Verified subset: Best@8 73.8% vs Random@8 57.9%. With early stopping, +17.7 pts over random while averaging 1.35 attempts vs 8.0. AUC on production outcomes: benchmark-only training ~0.45-0.48 (near random), PR-merge supervision 0.58, code-survival supervision 0.69. — [OpenHands blog 2026](https://openhands.dev/blog/20260305-learning-to-verify-ai-generated-code)
- LLM-as-a-Verifier (arXiv 2607.05391, 6 Jul 2026): scores via expected value over scoring-token logits rather than discrete judgments; reports 78.2% SWE-bench Verified and 86.5% Terminal-Bench 2.0 (self-reported). Verifier/generator models, n and cost not in abstract. — [arXiv 2607.05391](https://arxiv.org/abs/2607.05391)
- Scout / Traverse (arXiv 2609.17930, 15 Sep 2026): 2,518 trajectories, 6,967 mistakes, 78 failure types across SWE, computer use and science. Agents rarely recover after their first mistake and rarely catch it. Six frontier judges struggle; the best locates the first mistake in fewer than a third of runs. A 4B verifier (Scout) locates failures better, raises success above single-attempt when used for selection, and is claimed to transfer to unseen domains. Exact selection numbers and cost not in abstract (search snippet claimed 78.0% SWE-bench Verified; unverified). — [arXiv 2609.17930](https://arxiv.org/abs/2609.17930)

### Inferences
- [inferred] Early stopping with a critic (1.35 attempts vs 8) is the closest published analogue to "cheap first, escalate on doubt": it shows a good critic can stop sampling early without losing selection gains. But it needs a critic with real discrimination; the project's judge has none on its 8 ship calls.
- [inferred] Frontier judges finding the first mistake in under a third of runs (Scout) is consistent with Opus-as-judge failing on GLM output; a stronger judge model alone is not the fix.
- [inferred] A critic trained on SWE-bench traces should not be assumed to transfer to the project's Terminal-Bench-style held-out tasks (OpenHands AUC ~0.45-0.48 out of distribution).

### Gaps
- Full LLM-as-a-Verifier and Scout papers were not readable (compressed PDF); generator models, n, cost and per-benchmark selection numbers unconfirmed.

## Q4. Results on SWE-bench Verified / Terminal-Bench with cost figures

### Takeaway
Cost-reported results exist for older SWE-bench work (Agentless $0.70/issue on Lite; CodeMonkeys ~$2,300 for Verified); Terminal-Bench cost-per-solve data is mostly third-party estimates.

### Cited Findings
- Agentless: 32.00% SWE-bench Lite, $0.70 average. — [Agentless](https://arxiv.org/abs/2407.01489)
- CodeMonkeys: 57.4% SWE-bench Verified, $2,291.90 total. — [CodeMonkeys HTML](https://arxiv.org/html/2501.14723)
- Long-Horizon-Terminal-Bench 2.0 (arXiv 2610.14146; a different benchmark from Terminal-Bench 2.0): estimated cost per solved task from token usage x list price ranges from $19 to $838 across models (per search summary; not opened). — [arXiv 2610.14146](https://arxiv.org/pdf/2610.14146)
- Repeated attempts multiply cost linearly: a model needing three attempts effectively costs 3x its per-attempt price (vendor blog). — [Kilo blog](https://blog.kilo.ai/p/terminal-bench-scores-are-now-in)

### Inferences
- [inferred] CodeMonkeys' total implies about $4.58 per instance across 500 instances.

### Gaps
- No official Terminal-Bench 2.0 cost-per-solve table found; Snorkel leaderboard entries could not be verified.

## Q5. Expected effect for a cascade where most cost is the strong-model rescue

### Takeaway
Cascade theory says gain comes from the deferral score's discrimination (AUROC), and two levels capture nearly all of it. With a cheap pass@1 of ~6% and a judge with 0 correct ships out of 8, the cheap stage cannot cut rescue cost; spending more cheap compute only helps if a verifier with real precision is added.

### Cited Findings
- "Is Escalation Worth It?" (arXiv 2605.06350, v1 7 May 2026, v2 5 Oct 2026): two-model cascades beat single-model selection by 2.1-8.2 pts mean test accuracy; more than two models did not improve accuracy in 118 of 135 comparisons (at most +0.43 pts). Gain over random escalation decomposes into AUROC terms; deeper-cascade gains vanish when scores reflect difficulty shared across models. Benchmarks not named in abstract; no dollar costs. — [arXiv 2605.06350](https://arxiv.org/abs/2605.06350)
- Self-reported confidence is a weak escalation signal; separate verifiers do better (practitioner evidence, small n). — [GitHub routing benchmark](https://github.com/dennisonbertram/llm-model-routing-benchmark/tree/main/03-pocs/L3a-frugalgpt-cascade)
- Agents rarely recover after the first mistake (Scout). — [arXiv 2609.17930](https://arxiv.org/abs/2609.17930)

### Inferences
- [inferred] Expected saving per task ~= P(cheap correct and verifier ships) x (strong cost avoided) - (cheap cost + verifier cost) - P(false ship) x (penalty of a wrong answer). With P(cheap correct) ~6% and ship precision 0/8, the first term is near zero, so every cheap token and judge call is pure overhead on top of the Opus rescue.
- [inferred] Raising cheap pass@k (n samples) raises the first term only if the verifier can pick the right sample; SWE-Gym/CodeMonkeys show real selectors recover ~half the oracle gap even at much higher base rates.
- [inferred] Highest-leverage options suggested by the literature: (a) make the judge strict (ship only with execution evidence, hybrid test + judge), accepting more handovers, to stop false ships; (b) shorten the rescue rather than avoid it: hand Opus the cheap model's localisation/context (CodeMonkeys spent 15.4% on context with a local model) instead of the cheap model's patch, since agents rarely recover from an early mistake; (c) skip the cheap attempt on task types where the cheap model's measured pass rate is near zero, because a two-level cascade only pays when the deferral score separates easy from hard.

### Gaps
- No published cascade study on SWE-bench Verified or Terminal-Bench reports cost vs resolve rate for cheap-first with strong-model takeover.
