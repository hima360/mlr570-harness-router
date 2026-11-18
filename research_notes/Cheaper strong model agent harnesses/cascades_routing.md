# Model cascades and learned routing between cheap and strong LLMs, for multi-step agent tasks

Scope: methods that cut strong-model cost while keeping quality; reported numbers as published; how well each transfers to agentic (multi-step) settings. Researched 2026-11-18. Every number below is as published unless marked "Inferred". arXiv dates are v1 submission dates.

Our setup, for reference (from the assignment, not re-verified here): GLM-5.3 Flash first, Opus 5.5 judges once and takes over on failure. Held-out 11/36 solved vs Opus alone 4/35; cost/solve $3.80 vs $7.83; total cost +34% vs Opus alone; 94% of cost is the Opus takeover, 55% of that is Opus output tokens.

## Q1. FrugalGPT, RouteLLM, AutoMix, Hybrid LLM and the classic cascade/deferral literature: savings, quality loss, benchmarks

### Takeaway
The classic papers (2023-2024, all older results) report 40-98% cost cuts at little or no quality loss, but every one is single-query QA/chat (MT-Bench, MMLU, GSM8K, reasoning QA). None measures multi-step agents, and their headline numbers are "best case" (one benchmark, one model pair, often relative to a random-routing baseline rather than to strong-only).

### Cited Findings
- FrugalGPT (Chen, Zaharia, Zou; arXiv 2305.05176, 9 May 2023; older result): an LLM cascade can match GPT-4 with "up to 98% cost reduction", or improve accuracy over GPT-4 by 4% at the same cost; API prices differ by two orders of magnitude. — [arXiv 2305.05176](https://arxiv.org/abs/2305.05176)
- RouteLLM (Ong et al.; arXiv 2406.18665, 26 Jun 2024, v4 23 Feb 2025; older result): routers trained on human preference data plus augmentation cut cost "by over 2 times in certain cases" without compromising quality. — [arXiv 2406.18665](https://arxiv.org/abs/2406.18665)
- RouteLLM blog numbers (GPT-4 Turbo strong, Mixtral 8x7B weak): cost reduction vs GPT-4-only of over 85% on MT Bench, 45% on MMLU, 35% on GSM8K, at a target of 95% of GPT-4's performance. On MT Bench the best router needed 14% GPT-4 calls (with LLM-judge augmentation) vs 26% without. — [LMSYS blog, 1 Jul 2024](https://lmsys.org/blog/2024-07-01-routellm/)
- AutoMix (arXiv 2310.12963, 19 Oct 2023, v5 19 Jan 2025; older result): self-verification by the small model plus a POMDP meta-verifier decides whether to escalate; "over 50%" cost reduction "for comparable performance" across five LMs and five datasets (reasoning/QA). — [arXiv 2310.12963](https://arxiv.org/abs/2310.12963)
- Hybrid LLM (Ding et al.; ICLR 2024; arXiv 2404.14618; older result): a query-difficulty router allows "up to 40% fewer calls to the large model, with no drop in response quality". — [arXiv 2404.14618](https://arxiv.org/abs/2404.14618)
- Router over-selection: learned routers drift toward the strongest, most expensive model under loose budgets even when cheaper models give comparable utility ("strong-model over-selection"), and this persists on training data, so it is not only a generalisation error (arXiv 2602.03478, 3 Feb 2026, v2 5 Nov 2026; abstract has no numbers). — [arXiv 2602.03478](https://arxiv.org/abs/2602.03478)

### Inferences
- Inferred: these numbers do not transfer directly to our setting. All are per-query decisions with one model output per query; in agents, cost is dominated by turn count and cached context (see Q2, TurnCost), and a wrong cheap-model decision costs a whole failed trajectory plus a takeover, not one bad answer.
- Inferred: AutoMix is the closest classic analogue to our design (cheap first, self-verify, escalate). Our "Opus judges once" is a strong-model verifier rather than a cheap one; AutoMix's premise is that the verifier must be much cheaper than the strong answer.

### Gaps
- Exact per-benchmark AutoMix and FrugalGPT tables were not re-read; only abstract-level numbers are reported here.

## Q2. Routing for agents: trajectory-level vs step-level, mid-trajectory switching, escalation triggers; results on SWE-bench, Terminal-Bench, OSWorld etc.

### Takeaway
2026 agent-routing work splits into (a) trajectory-level "run cheap for K turns, then decide" (SWE-Router), (b) step-level gating with uncertainty/verifier features (R2V, StepGate, LEASH), and (c) subtask/phase routing (RSI-Router, LiteLLM blog). Reported gains are real but small and on small test sets; step-level baselines often lose to strong-only; and two 2026 papers show that (i) prompt-cache economics can make per-step switching cost more, and (ii) evaluating switching by replaying logs is invalid.

### Cited Findings
Trajectory-level, cheap-first escalation (closest to our cascade):
- SWE-Router (Son et al., UCL; arXiv 2607.00053, 30 Jun 2026; ICML 2026 DL4Code workshop): the cheap model runs K exploratory turns in mini-SWE-agent; a value head (Qwen2.5-Coder-7B) reads the partial trajectory and predicts whether the cheap model will finish; escalate if below a cost-adjusted threshold. Argues prompt-only routers hit a Bayes-error floor because "a similar issue can hide either a localized typo or a multi-module refactor". — [arXiv 2607.00053](https://arxiv.org/pdf/2607.00053)
- SWE-Router numbers (SWE-bench Verified test split; pairs gpt-5-mini→gemini-3-pro-preview and deepseek-v3.2→gemini-3-pro-preview): Route-AUC 0.780 at K=2 vs 0.627 for its own K=0 (prompt-only) variant on the deepseek pair (+15.3 pp); about +12 pp over the strongest baseline on the gpt-5-mini pair (0.709 at K=4 vs 0.565 Embed+LR). Prompt-only embedding routers score AUROC 0.477-0.567 on SWE-bench Verified; SWE-Router AUROC 0.586-0.617. Authors note AUROC is "not strongly correlated" with routing performance. The routing curve "briefly passes above the all-strong" point because weak and strong solve non-identical subsets. — [arXiv 2607.00053](https://arxiv.org/pdf/2607.00053)
- SWE-Router data: full SWE-bench Verified runs cost $16.6 (gpt-5-mini, 55.6%), $22.6 (deepseek-v3.2, 62.4%), $391.3 (gemini-3-pro-preview, 70.4%) for 500 instances. — [arXiv 2607.00053, Table 1](https://arxiv.org/pdf/2607.00053)

Step-level gating:
- R2V-Agent (arXiv 2605.16604, 15 May 2026): SLM acts, a calibrated MLP router (15 features: token entropy/log-prob, verifier score stats over K=5 candidates, candidate consistency, semantic entropy, step index, horizon fraction, context length) escalates steps to a teacher LLM. Averaged over four 7-14B SLMs. TerminalBench (89 tasks): SLM-only 81.1% SR; R2V 93.3% SR with 33.9% of steps escalated; LLM-only 98.1%; heuristic router 95.0% at 66.0%; entropy-only router 87.4% at 4.1%; oracle 97.8% at 18.4%. No dollar costs reported. — [arXiv 2605.16604](https://arxiv.org/html/2605.16604)
- R2V ablations: removing verifier or entropy features drops TextWorld to 62.6% SR; entropy alone stays at SLM-only level (64.6%) there. Consistency regularisation on TerminalBench: SR 93.8%→95.7% while LLM calls fell 35.7%→16.3%. No separate OOD benchmark; uses held-out splits and perturbations. — [arXiv 2605.16604](https://arxiv.org/html/2605.16604)
- StepGate (arXiv 2610.07816, 6 Oct 2026; workshop): per-step gate decides local / local verify-retry / cloud. Qwen2.5-1.5B local, Qwen2.5-7B strong. Multi-turn (100 scripted tasks, 30% cloud budget): local-only 48.0%, query-level router 57.0%, random escalation 60.0%, self-consistency 66.0% (38% cloud), StepGate 69.0%, strong-only 82.0%. StepGate vs random +9.0 pts (CI [-0.6, 18.8], p=0.108, not significant); vs query-level +12.0 (p=0.029). Token uncertainty alone: AUROC 0.565, the worst feature set. — [arXiv 2610.07816](https://arxiv.org/html/2610.07816)
- LEASH (arXiv 2610.22280, 15 Oct 2026): computer-use (OSWorld); weak agent escalates when latent (hidden-state) uncertainty is high, measured as closeness to past failed trajectories' activations. Qwen3.5-35B-A3B + LEASH 73.66% vs always-on Claude Opus 5 72.90%, at $0.4117/task, 19.2% cheaper than always-strong; beats random escalation by 6.55-11.94 points at matched escalation rate. — [arXiv 2610.22280](https://arxiv.org/abs/2610.22280)

Subtask/phase routing:
- RSI-Router (arXiv 2609.34712, 28 Sep 2026): evolves subtask definitions, per-subtask model assignment and model-specific "skills"; the small model routes at inference. DeepSeek-V4.1-Flash (large) / Qwen3.5-9B (small), mini-SWE-agent. SWE-bench Verified (32 test tasks): large-only 83.33% / $3.46, small-only 62.50% / $0.69, RSI-Router 84.38% / $3.18 (-8.1% cost). Terminal-Bench 2.0 (25 tasks): large-only 40.00% / $17.10, small-only 5.33% / $0.17, RSI-Router 46.67% / $14.02 (-18.0% cost). Step-level baselines below large-only: random step-level 28.00% TB2 ($6.87), Router-R1 10.67% ($0.31), MTRouter 24.89% ($5.85). Small-model prices are estimated via a 1:30 GPU-hour ratio, not list prices. — [arXiv 2609.34712](https://arxiv.org/html/2609.34712)
- RSI-Router ablation (same paper): removing execution skills raised scores by 9.7 (SWE-V) and 10.5 (TB2) points but increased cost by 33.7% and 33.9% of large-only cost. A search-engine summary presented this ablation as the headline result; the main table says the opposite (cheaper). — [arXiv 2609.34712](https://arxiv.org/html/2609.34712)
- RSI-Router: the small model's own difficulty judgments did worse than random step-level selection on all five benchmarks; the last three steps were enough to recognise the subtask. Code-repair split: large model localises and implements, small model verifies. — [arXiv 2609.34712](https://arxiv.org/html/2609.34712)
- LiteLLM vendor blog (not peer reviewed): phase routing (explore / implement / verify) on 12 SWE-bench Verified tasks matched fixed Claude Opus quality at 46% less cost, edits only on the frontier model. — [LiteLLM blog](https://docs.litellm.ai/blog/subtask-type-routing)

Caveats on step-level switching:
- TurnCost (Kumar & Pandey; arXiv 2610.15093, 10 Oct 2026; POVC '26 at ASE 2026): 600 public coding-agent trajectories; 98.6% of input tokens are prefix-cache hits, so flat input pricing overstates loop cost about 7× (6.5-7.8×). Turn count explains 84% of variance in log trajectory cost vs 4% for mean observation size. Delegating turns to a cheaper model "increases cost in half of the evaluated settings" because switching loses the strong model's warm cache; derives a break-even horizon (delegate only if remaining turns exceed p_weak_in / p_strong_cache). Notes routing papers report savings "as high as 72%", mostly under flat pricing. — [arXiv 2610.15093](https://arxiv.org/pdf/2610.15093)
- The Replay Gap (Gonuguntla, CMU; arXiv 2608.08239, 8 Aug 2026; COLM 2026 workshop): forking live SWE-bench trajectories and swapping models rewrites 61-94% of post-fork actions; 74-77% of early swaps diverge at the first post-fork action; only about 3% of replayed states stay valid; a log-stitching replay evaluator "mispredicts every success-relevant outcome call". Absolute resolve rates in this regime were 0-3%. — [arXiv 2608.08239](https://arxiv.org/pdf/2608.08239)
- Open-source StaySwitch project treats cache state and switching cost as part of the per-step decision (no numbers checked). — [GitHub stayswitch](https://github.com/HanyangWang0418-oss/stayswitch)

### Inferences
- Inferred: no primary paper found that routes on tau-bench or WebArena with a cheap/strong switch; leaderboard and blog results surfaced were unreliable and are excluded.
- Inferred: our cascade is a trajectory-level K=whole-trajectory version of SWE-Router with a strong-model judge. SWE-Router's main lesson is to decide after a few cheap turns rather than at the end, which would cut cheap-model waste but not the Opus takeover, which is 94% of our cost.
- Inferred: the takeover is mid-trajectory switching, so TurnCost's cache argument applies: Opus starts cold on a long transcript. Handing Opus a short summary instead of the full transcript cuts cold prefill, but our cost is 55% Opus output tokens, so the bigger lever is fewer Opus turns.
- Inferred: any router we evaluate offline by re-scoring logged runs is invalid for step-level decisions (Replay Gap). Only task-level decisions (run cascade vs Opus-only vs skip) can be scored on logs, because those are whole independent trajectories.

### Gaps
- No dollar-cost results for R2V; StepGate's strong model is only a 7B model, so it says little about frontier-model economics.
- No primary results found on WebArena or tau-bench for cheap/strong switching.
- Small test sets everywhere: RSI-Router TB2 n=25, SWE-V n=32; StepGate single-step n=52.

## Q3. Do learned routers generalise across benchmarks and task distributions?

### Takeaway
Weakly. Routers transfer across model pairs better than across task distributions. Out-of-distribution queries degrade every router, simple kNN degrades least, and in agents the same trajectory-conditioned router lost its edge on a repository-split test set.

### Cited Findings
- RouteLLM: routers trained on Chatbot Arena data were "near-random" on MMLU (out of distribution); about 1,500 golden-label samples fixed it. The same routers kept performance when the model pair changed to Claude 3 Opus / Llama 3 8B. — [LMSYS blog](https://lmsys.org/blog/2024-07-01-routellm/)
- Cross-dataset on RouterBench (arXiv 2505.12601, 2025; older result): train on one dataset, test on the others; all routers degrade; kNN (k=100) drops least (2.63 points), matrix factorisation most (6.67). — [arXiv 2505.12601](https://arxiv.org/pdf/2505.12601)
- SWE-Router: on the SWE-Smith test split (different repositories from training), the gpt-5-mini-pair router did not beat baselines on Route-AUC (0.493-0.547 vs 0.591 Embed+LR); authors attribute this to distribution shift between repository splits. Its SWE-bench Verified gains are on a different benchmark than training (SWE-Smith), so they are partly out of distribution. — [arXiv 2607.00053](https://arxiv.org/pdf/2607.00053)
- RouteFM (arXiv 2609.37362, 29 Sep 2026): a pretrained "foundation" router adapting by context beat the strongest baseline by 2.23 quality points on MMR-Bench, held out of pretraining, with eight observations per candidate. — [arXiv 2609.37362](https://arxiv.org/abs/2609.37362)
- Over-selection of the strongest model also appears on training data (EquiRouter). — [arXiv 2602.03478](https://arxiv.org/abs/2602.03478)

### Inferences
- Inferred: a router trained on our handful of Terminal-Bench dev tasks should be expected to fall to near-random on held-out tasks, matching our own failed attempt to predict which takeovers succeed. With n≈36, one task is about 2.8 points of solve rate, below the effect sizes in Q5.

### Gaps
- No study found that trains an agent router on one agentic benchmark (e.g. SWE-bench) and tests it on another (e.g. Terminal-Bench).

## Q4. Which escalation signals actually predict when a strong model is needed in agent runs?

### Takeaway
Task-description-only signals are near chance for coding agents (AUROC ≈0.48-0.57). Signals from the cheap model's own early trajectory (observations, test output, verifier scores, candidate disagreement, hidden-state similarity to past failures) do better but are modest (AUROC ≈0.6-0.83, Route-AUC up to 0.78). Token entropy alone is consistently the weakest. Self-reported difficulty from a small model is worse than random.

### Cited Findings
- Prompt-only routers on SWE-bench Verified: AUROC 0.477-0.567; partial-trajectory value head after 1-4 cheap turns: 0.586-0.617 AUROC but Route-AUC 0.69-0.78. — [arXiv 2607.00053](https://arxiv.org/pdf/2607.00053)
- Verifier-score features matter most in R2V (removing them drops TextWorld SR to 62.6%); entropy-only router barely escalates (4.1% of steps on TerminalBench, 87.4% SR vs 93.3% for full R2V). — [arXiv 2605.16604](https://arxiv.org/html/2605.16604)
- StepGate: all features AUROC 0.829; token uncertainty alone 0.565; sample consistency and schema validity changed nothing at matched budget on its split. — [arXiv 2610.07816](https://arxiv.org/html/2610.07816)
- LEASH: hidden-state distance to past failed vs successful trajectories beats random escalation by 6.55-11.94 points at matched rate (needs open weights). — [arXiv 2610.22280](https://arxiv.org/abs/2610.22280)
- RSI-Router: small-model difficulty self-judgments underperformed random step selection on all five benchmarks; subtask identity (from the last three steps) was the useful signal. — [arXiv 2609.34712](https://arxiv.org/html/2609.34712)
- AutoMix: cheap-model self-verification as escalation trigger (QA setting; older result). — [arXiv 2310.12963](https://arxiv.org/abs/2310.12963)

### Inferences
- Inferred: for us (GLM via API, no hidden states), the usable signals are early-trajectory features and test/check outcomes. The best-supported change is moving the escalation decision earlier (after K cheap turns, not after a full failed run), not predicting whether Opus will succeed.
- Inferred: none of these papers predicts whether the strong model will succeed after takeover; all predict whether the cheap model will fail. Our "no separation" result on takeover success has no counter-evidence in the literature.

### Gaps
- No paper found that uses test-execution results as the escalation signal on Terminal-Bench with a frontier strong model and reports dollar cost.

## Q5. Expected effect size of a task- or step-level router on a cascade like ours

### Takeaway
Inferred from the numbers above: expect single-digit to about 20% cost reduction at roughly matched solve rate, not the 40-98% of QA papers. Frontier-agent results range from -8.1% (RSI-Router SWE-V) to -19.2% (LEASH OSWorld) to -18.0% (RSI-Router TB2), on 25-35-task test sets. With 94% of our cost in the Opus takeover, a router that only changes the GLM side cannot move total cost much.

### Cited Findings
- RSI-Router: -8.1% cost at +1.05 pts (SWE-V, n=32); -18.0% cost at +6.67 pts (TB2, n=25). — [arXiv 2609.34712](https://arxiv.org/html/2609.34712)
- LEASH: -19.2% cost at +0.76 pts vs always-on Claude Opus 5 (OSWorld). — [arXiv 2610.22280](https://arxiv.org/abs/2610.22280)
- R2V TerminalBench: 33.9% of steps escalated for 93.3% vs 98.1% SR (-4.8 pts); no dollar figures. — [arXiv 2605.16604](https://arxiv.org/html/2605.16604)
- TurnCost: under cache-aware pricing, delegation increased cost in half the settings. — [arXiv 2610.15093](https://arxiv.org/pdf/2610.15093)
- SWE-Router: cheap+strong routing can beat strong-only on resolve rate because the two solve different subsets. — [arXiv 2607.00053](https://arxiv.org/pdf/2607.00053)

### Inferences
- Inferred, arithmetic on our numbers: GLM is about 6% of our cost, so even a perfect "skip GLM on tasks it cannot help" router saves at most about 6% of total cost. The only way to beat Opus-alone total cost is to avoid Opus takeovers that fail or to shorten them (Opus output is about 52% of total cost: 0.94 × 0.55).
- Inferred: a task-level "don't take over" rule only saves money if it can spot failing takeovers; our data showed no separation, and the literature gives no signal that predicts strong-model success. A reasonable estimate for a learned router on n≈36 held-out tasks is an effect inside noise (±1-2 tasks).
- Inferred: the literature's most transferable gain matching our failure profile is SWE-Router-style early escalation plus TurnCost-style turn reduction for the strong model. Neither addresses Opus output tokens directly.

### Gaps
- No published cascade with a frontier strong model on Terminal-Bench reports both total-cost and cost-per-solve, so our "cost per solve better, total cost worse" pattern has no direct comparison point.
