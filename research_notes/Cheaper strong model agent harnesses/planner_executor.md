# Strong model directs, cheap model acts: planner/executor, architect/editor, advisor/executor splits in coding and terminal agents

Research date: 2026-11-18. All numbers are copied as published; anything computed or inferred is marked "(inferred)". Most pages were read via WebFetch summaries (Source inspected, 2026-11-18), not full PDFs.

## Q1. Aider architect/editor mode: published numbers for strong-architect + cheap-editor vs strong alone

### Takeaway
Aider's architect/editor split works because the architect still writes the solution (in prose/code) and the editor only turns it into well-formed file edits; it raised scores over the architect alone in most pairs, and the best cost result was R1 architect + Sonnet editor at 64.0% for $13.29 vs o1 alone 61.7% for $186.5 (Jan 2025, old models). It is a formatting split, not an output-token saving on the strong side.

### Cited Findings
- Published 2024-09-26 (old; code-editing benchmark, pre-polyglot). Architect/editor pass rates vs architect solo: o1-preview + o1-mini (whole) 85.0%; o1-preview + deepseek (whole) 85.0%; o1-preview + claude-3.5-sonnet (diff) 82.7%; o1-preview + deepseek (diff) 80.5%; o1-preview solo 79.7%. claude-3.5-sonnet + deepseek (diff) 78.9% vs Sonnet solo 77.4%; Sonnet + Sonnet 80.5%. gpt-4o + deepseek (diff) 74.4% vs gpt-4o solo 71.4%. o1-mini + deepseek (whole) 71.4% vs o1-mini solo 61.1%. — [Aider: Separating code reasoning and editing](https://aider.chat/2024/09/26/architect.html)
- Same post publishes no cost figures; says the top o1-preview "whole" pairings are too slow for interactive use. Rationale given: o1 models reason well but emit badly formatted edits; the architect describes the solution freely and the editor formats it. Pairing a model with itself also helped (Sonnet, GPT-4o, GPT-4o-mini). — [Aider, 2024-09-26](https://aider.chat/2024/09/26/architect.html)
- Published 2025-01-24 (old), aider polyglot: R1 architect + Sonnet editor 64.0% at $13.29 total; o1 (high effort) 61.7% at $186.5; R1 alone 56.9% at $5.42; Sonnet alone 51.6% at $14.41; DeepSeek V3 48.4% at $0.34. Post says o1 + Sonnet did NOT beat o1 alone, and other editor models did not lift o1 or R1 over their solo scores. R1 thinking tokens were not passed to the editor; runs that did pass them scored worse. — [Aider: R1+Sonnet sets SOTA](https://aider.chat/2025/01/24/r1-sonnet.html)

### Inferences
- In the 2025 result the "strong" architect (R1) was the cheap model and the editor (Sonnet) the pricier one; the gain came from combining a good reasoner with a reliable editor, not from a strong model offloading typing to a cheap one. (inferred)
- In architect mode the architect still emits the full solution, so architect output tokens are not reduced; this does not address the project's problem that 55% of cost is strong-model output. (inferred)
- Passing more of the strong model's raw reasoning to the editor hurt — a handoff-content finding relevant to "problem list" style handoffs. (inferred from the R1 thinking-token note)

### Gaps
- No Aider architect/editor results found for 2026 frontier models (Opus 5.x, GLM-5.x); the leaderboard page was not re-checked this session.
- Aider polyglot is single-file code editing, not multi-turn terminal tasks.

## Q2. Anthropic advisor tool / advisor strategy and similar vendor patterns

### Takeaway
Anthropic's advisor tool (launched 2026-04-09) inverts the split: the cheap executor runs the whole agent loop and calls the strong model only for short advice (typically 400-700 text tokens), which is the published design that most directly cuts strong-model output. Published gains are modest (Sonnet + Opus advisor: +2.7 pp on SWE-bench Multilingual at -11.9% cost per task vs Sonnet alone); no published comparison vs Opus solo.

### Cited Findings
- Published 2026-04-09. Sonnet 4.6 + Opus advisor vs Sonnet solo on SWE-bench Multilingual: +2.7 percentage points, cost per agentic task down 11.9%. BrowseComp and Terminal-Bench 2.0: higher score and lower cost per task, exact numbers only in charts. Haiku 4.5 + Opus advisor on BrowseComp: 41.2% vs 19.7% solo; trails Sonnet solo by 29% but costs 85% less per task. Opus solo scores/costs are not reported; Anthropic recommends evaluating Sonnet solo vs Sonnet+advisor vs Opus solo yourself. — [Anthropic: The advisor strategy](https://claude.com/blog/the-advisor-strategy)
- Secondary write-ups give Sonnet 4.6 High solo 72.1% / $1.09 per task vs 74.8% / $0.96 with Opus advisor on SWE-bench Multilingual (not seen on the primary page in text form; consistent with +2.7 pp / -11.9%). — [Daily Dose of DS](https://blog.dailydoseofds.com/p/advisor-strategy-in-agents); [besthub.dev](https://www.besthub.dev/articles/how-anthropic-s-advisor-strategy-boosts-sonnet-scores-by-2-7-while-cutting-costs-12-dddcc5a7c64e)
- Mechanics: tool type `advisor_20260301`, beta header `advisor-tool-2026-03-01`; executor decides when to call; advisor reads the full transcript, never calls tools, returns a plan, correction or stop signal; all inside one /v1/messages request. — [Anthropic blog](https://claude.com/blog/the-advisor-strategy); [Advisor tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool)
- Advisor output: usually 400-700 text tokens (1,400-1,800 incl. thinking). Uncapped on hard reasoning tasks the mean was ~4,200-5,900 tokens; with advisor `max_tokens: 2048` ~630-840 tokens with ~0% truncation; with 1024 ~370-480 with ~10% truncation. Advisor billed at advisor rates, reported in `usage.iterations[]`. — [Advisor tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool)
- Prompting guidance: call the advisor early (after a few orientation reads, before committing to an approach) and again after writes/test output, before declaring done; coding prompts target ~2-3 calls per task. A turn-2 reminder for Haiku raised pass rate ~7 points on Haiku 4.5, no effect on Sonnet, slightly lowered Opus. A Haiku coding variant raised coding pass rates but cost ~4 points on browse-heavy work. "Keep guidance under 80 words" soft cap is supported. — [Advisor tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool)
- Stated limits: not a fit for single-turn Q&A or workloads where every turn needs full capability; benefit shrinks as executor approaches advisor capability. Executor must be a Claude model; supported advisors include Opus 5.5. 5-series advisors return encrypted advice the client cannot read. — [Advisor tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool)
- Microsoft SWE-Edit (arXiv 2604.26102, v2 2026-05-26): GPT-5 main agent plans; GPT-5-mini Viewer and Editor subagents do file reading and edit application. SWE-bench Verified: baseline 69.9% at $243.7 → SWE-Edit 72.0% (+2.1 pp) at $200.1 (-17.9%). Editor-only variant: 71.3% but +10.1% cost (main agent inspected files more). Upgrading the Editor to GPT-5 gained only +0.4 pp at ~5.8x cost. On three other reasoning models: +1.6 to +4.1 pp (100 instances). — [SWE-Edit](https://www.alphaxiv.org/abs/2604.26102.md)
- Cursor agent swarm (reported 2026-07-26): frontier planners (Opus 4.8 or Fable 5) + Composer 2.5 workers rebuilt SQLite in Rust; all new-swarm configs reached 100% on sqllogictest. Workers used >=69% of tokens (usually >90%). Opus hybrid total $1,339 vs GPT-5.5 solo $10,565 and Fable 5 solo ~$20,057. In the Opus hybrid the planner was a small share of tokens but ~two-thirds of the bill. Old swarm failures: split-brain duplicate features, 70,000+ merge conflicts. — [The Decoder](https://the-decoder.com/cursors-agent-swarm-suggests-cheaper-models-can-handle-most-coding-when-frontier-models-plan-the-work/) (secondary report of Cursor's post)

### Inferences
- Advisor pattern is the closest published match to "cheap model runs, strong model directs" with strong-model output bounded by design (capped short advice), vs the project's current "strong model takes over" which pays full Opus output. (inferred)
- The advisor tool itself cannot use GLM as executor (Claude-only executors), so for this project it would have to be reimplemented in the harness: GLM loop + an "ask_advisor" tool that calls Opus with the transcript and a hard output cap. (inferred)
- SWE-Edit and Cursor both show the cheap model taking over low-judgment sub-work (reading/editing/bulk implementation) while the strong model keeps the loop; the strong model's share of cost still dominates in Cursor (~2/3). (inferred)

### Gaps
- No primary OpenAI (Codex) or Google (Gemini CLI/Jules) published planner/executor benchmark with pass rate + cost was found.
- No published advisor-strategy number vs Opus solo, and no Terminal-Bench numbers in text form (chart only).

## Q3. DeepSeek agent harness; Claude Code subagents, Codex, OpenHands, SWE-agent multi-model configs

### Takeaway
DeepSeek Harness (DSH, released ~2026-08-13, developer preview) is a single-model coding runtime; its published Terminal-Bench figures are single-model, and a "strong orchestrator + Flash subagents" configuration is described only in secondary blogs. The best direct Terminal-Bench evidence for a split is a vendor blog: Opus 4.8 orchestrator + GLM-5.2 executor 69.7 mean vs GLM-5.2 solo 58.4, at 2.9x the cost (no Opus solo given).

### Cited Findings
- DSH: open-source runtime released 2026-08-13 alongside V4-Pro; presets include Standard (file editing, shell, search, planning, subagents) and Minimal (persistent bash + file edit), the latter used for V4 Flash's Terminal-Bench 82.7. DeepSeek reports V4-Pro 87.9 on Terminal-Bench 2.1; Artificial Analysis reproduced 79 with its own harness. DSH kills background processes at task end, breaking three Terminal-Bench tasks. — search summaries of [The New Stack](https://thenewstack.io/deepseek-harness-cleanup-benchmark/) (403 on fetch), [HPCwire](https://hpcwire.com/bigdatawire/2026/08/14/deepseek-open-sources-the-missing-layer-between-ai-models-and-agents), [Artificial Analysis](https://artificialanalysis.ai/agents/coding-agents/deepseek-harness) (Unverified: not fetched)
- Secondary sources describe a stronger orchestrator for planning with V4 Flash for parallel subagents, and default model Flash; sources disagree on whether the repo is official. Prices: V4-Flash $0.08/$0.18 per 1M in/out on DeepInfra vs V4-Pro $1.30/$2.60; another source $0.14/$0.28 for Flash on DeepSeek's API. — [teamorouter](https://teamorouter.com/blogs/deepseek-harness-agent-framework); [DeepInfra review](https://deepinfra.com/blog/deepseek-harness-review); [tenten](https://developer.tenten.co/deepseek-harness-workflow-agent-teams) (Unverified: search snippets only)
- Blackbox (2026-06-22), Terminal-Bench 2.0 full suite: Opus 4.8 orchestrator (explore, plan, delegate, verify) → GLM-5.2 executor: 69.7 mean, 62/86 passes, $2.40/task. Opus 4.8 → Kimi K2.7: 58.4, 52/89, $1.85. GLM-5.2 solo: 58.4, 52/89, $0.84. Swapping the orchestrator to GPT-5.5 roughly halves the score. Pass denominator 86 vs 89 unexplained. No Opus 4.8 solo baseline given. — [Blackbox](https://www.blackbox.ai/blog/orchestrator-executor-multi-agent-system-for-swe-tasks) (vendor blog)
- Akita (2026-04-25), practitioner test, Rails app, 0-100 rubric: Opus 4.7 solo (opencode) 97, 18m, ~$4.04. Forced delegation: Opus+Kimi K2.6 95 (~$3-4); Opus+GLM 5.1 93 (13m, ~$0.50 + subscription); Opus+Sonnet 4.6 (Claude Code) 92, $5.77; Opus+Haiku 4.5 90, $3.49; GPT-5.4 xHigh + medium (Codex) 94, ~$1-3. Manual Opus+Kimi tied 97 at ~$5-7. With free choice, the planner delegated zero times in all 7 variants. Opus+DeepSeek V4 Pro failed (opencode dropped `reasoning_content`). Conclusion: no mixed variant beat solo Opus; planner cost dominated. — [AkitaOnRails](https://akitaonrails.com/en/2026/04/25/llm-benchmarks-vale-a-pena-misturar-2-modelos/) (single task, n=1 per variant)

### Inferences
- If Opus solo on Terminal-Bench 2.0 is well above 69.7 (not checked), the Blackbox split is a "lift the cheap model" result, not a "match Opus cheaper" result. (inferred; Opus 4.8 TB2 score not looked up)
- Akita's "zero delegation when optional" says a strong model given a subagent will not hand off unless forced — relevant if the harness relies on the strong model choosing to delegate. (inferred)

### Gaps
- No primary DeepSeek README/paper read; the planner-with-Flash-subagents configuration and its numbers are unconfirmed.
- No published OpenHands or SWE-agent multi-model (strong planner + cheap executor) pass-rate/cost result was found in this pass.
- Claude Code subagent model splits (e.g. Opus main + Haiku subagents): no benchmark with numbers found beyond Akita.

## Q4. Plan-and-execute / hierarchical agents: cost and pass-rate findings

### Takeaway
Research results agree that a strong director over a weak worker can roughly match the strong single agent (62% vs 60% on SWE-bench Lite) using fewer strong tokens, but a strong-model-written plan handed to a weak model to execute in one shot lost badly (29.3% vs 45.3% strong alone); cascades where the strong model does the core work win.

### Cited Findings
- Liu, "Can AI Models Direct Each Other?" (arXiv 2603.26458, 2026-03-27): ManagerWorker on 200 SWE-bench Lite; manager is text-only (analyzes, dispatches exploration, reviews), worker has repo access. Strong manager + weak worker 62% vs strong single agent 60%; weak manager + weak worker 42% vs weak single agent 44%. Review-only loop +2 pp; structured exploration and planning +11 pp. Uses "a fraction of the strong-model token usage" (no figure). Author attributes limits to models trained as monolithic agents. Model names not on abstract page. — [arXiv 2603.26458](https://arxiv.org/abs/2603.26458)
- CMU strong-weak collaboration (arXiv 2505.20182, 2025-05-26; older), Agentless Lite on SWE-bench Lite (300), O4-mini strong + GPT-4o-mini weak: O4-mini alone 45.33% at $33.33; GPT-4o-mini alone 15.33% at $4.53. Strong LM First 41.67% at $25.34; Strong LM Single Attempt 38.00% at $23.62; Plan (strong plans, weak executes) 29.33% at $29.68; Instance-level QA pairs 25.67% at $25.60; Weak Router 24.00% at $17.90; Strong Router 21.33% at $36.34; Weak LM First 15.33% at $6.06; repo-level context methods 13.67-14.67%. O3-mini + Qwen2.5-Coder-32B: Strong LM First 33.00% at $27.74 vs O3-mini alone 33.67% at $46.22. Repo-level summaries "too coarse"; Strong Router overthinks; no single strategy best at all budgets. — [arXiv 2505.20182](https://arxiv.org/html/2505.20182v1)
- ReWOO (arXiv 2305.18323, 2023-05-23; old, QA not coding): decoupling planning from tool observations gave "5x token efficiency and 4% accuracy improvement" on HotpotQA, and let reasoning be distilled from 175B GPT-3.5 into 7B LLaMA. — [arXiv 2305.18323](https://arxiv.org/abs/2305.18323)
- Harness ablation study (arXiv id uncertain, Emergent Mind 2609.20804): 176 settings on SWE-Bench Verified and Terminal-Bench 2.1; context management matters more than planning under tight budgets; planning "an accuracy scaffold for weaker models and a cost saver for stronger ones". — [Emergent Mind](https://www.emergentmind.com/papers/2609.20804) (Unverified: search summary only)

### Inferences
- The project's failed variant (cheap model fixes from the strong model's problem list) resembles CMU's "Plan" and "QA pairs" strategies, which both lost ~16-20 pp vs strong alone; the published results that hold up keep the strong model in a loop (manager reviewing, advisor re-consulted) rather than one-shot handoff. (inferred)
- "Strong LM First" (strong attempts the core work, weak fills in) is closest to the project's current takeover design and was the best non-oracle strategy in the CMU study. (inferred; exact strategy definition not re-read)

### Gaps
- Model identities and token numbers for arXiv 2603.26458 not read from full text.
- No hierarchical-agent paper with Terminal-Bench pass rate and cost vs strong-alone found beyond the Blackbox vendor blog.

## Q5. When does the split fail (handoff loss, executor ignoring advice)?

### Takeaway
Reported failure modes: the strong model's plan is too coarse or loses instance detail; the executor does not ask for help (or the planner does not delegate); extra coordination tokens on the strong side eat the savings; and reformatted or summarised handoffs lose information. Splits help most when the capability gap is large and the cheap role is mechanical.

### Cited Findings
- Weak director + weak worker scores below weak single agent (42% vs 44%); the gain needs a real capability gap; models trained as monolithic agents resist director/worker roles. — [arXiv 2603.26458](https://arxiv.org/abs/2603.26458)
- Plan handoff to weak model: 29.33% vs strong alone 45.33%; repo-level summaries "too coarse" and may distract from instance-specific signal; Prompt Reduction cut valid patch rate to ~65% vs ~95%. — [arXiv 2505.20182](https://arxiv.org/html/2505.20182v1)
- Passing R1's thinking tokens to the editor made results worse; o1 + any editor did not beat o1 alone. — [Aider 2025-01-24](https://aider.chat/2025/01/24/r1-sonnet.html)
- Executor under-calls the advisor: Anthropic recommends a turn-2 reminder for Haiku (+~7 pts on Haiku 4.5) and says benefit shrinks as executor nears advisor capability; uncapped advice runs 4,200-5,900 tokens on hard tasks. — [Advisor tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool)
- Planner never delegates when optional (0 delegations in 7 variants); forced delegation never beat solo Opus; orchestration added time and hidden planner cost (~$11 across two manual runs); a provider-format bug (`reasoning_content` dropped) made one executor contribute nothing. — [AkitaOnRails](https://akitaonrails.com/en/2026/04/25/llm-benchmarks-vale-a-pena-misturar-2-modelos/)
- Adding a cheap Editor alone raised the strong main agent's file inspections and total cost (+10.1%); Viewer can over-prune needed code. — [SWE-Edit](https://www.alphaxiv.org/abs/2604.26102.md)
- Multi-planner split-brain: duplicate features built in different places, merge-conflict explosion; planner ~two-thirds of bill despite small token share. — [The Decoder on Cursor](https://the-decoder.com/cursors-agent-swarm-suggests-cheaper-models-can-handle-most-coding-when-frontier-models-plan-the-work/)

### Inferences
- For a "strong model directs, cheap model types" design to cut Opus output tokens, published evidence favours: (a) cheap model keeps the loop and the strong model gives short, capped advice at fixed points (before committing, before declaring done), and (b) the strong model's advice is re-consultable after the executor tries, rather than a one-shot problem list. (inferred)
- A design where Opus writes the exact code/commands and GLM only applies them (Aider-style) keeps correctness but saves little Opus output, since the code is the output. (inferred)

### Gaps
- No published study measures executor compliance with advice (how often the cheap model ignores or misapplies the strong model's instructions) on terminal tasks.
- No published result found for advisor-style consulting with a non-Claude cheap executor on Terminal-Bench.
