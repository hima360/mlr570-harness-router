# MLR570 final paper plan

Shared plan for the final paper (Ammar, Mahek, Ibrahim). Live version with comments:
https://claude.ai/artifact/PJquGemazUaT53paUnjazR. This file is the copy in the repo, 2026-11-21.
The LaTeX skeleton is in `paper/` (upload `paper.zip` to OpenAI Prism; see `paper/README.md`).

Rule for everyone: a writer adds wording, not new claims. Every number comes from the run logs
(`results/*.jsonl`, `jobs/*/result.json`) or from this file; cost is always tokens x public list price.

## Thesis

A harness is not a neutral container for a model. With the model held fixed, the harness changes pass rate,
cost and time. Phase 1 measures this across four existing harnesses. Phase 2 builds our own harness and shows
that its design choices (who works first, who reviews, when to hand over, which provider serves the model)
change results again with the same two models.

Working title: *Harness-Aware, Cost-Efficient Model Routing for Agentic LLM Systems* (the proposal's title).

## Research questions

- **RQ1.** For a fixed model, how much does the harness change pass rate, cost and time?
  - **RQ1a.** Is there one best harness, or does the best harness depend on the model?
  - **RQ1b.** Can a cheaper model in a well-matched harness equal a more expensive model?
- **RQ2.** Can a harness that divides work between a cheap and a strong model, using the same models, beat the
  strong model alone on solves and cost per solve?
- **RQ3 (exploratory).** With model and harness fixed, does the provider serving the model change pass rate,
  cost and time? Answered with existing runs only; a controlled test is future work.

## The story in 13 claims

1. **Intro.** Agents are ranked by model name, but every score comes from a model inside a harness (prompts,
   tools, memory, retries, turn limits). The harness is usually treated as a neutral container; we argue it is
   part of the system. If it matters this much, its design is a lever: it can decide which model does which part
   of the work and when to hand over.
2. **RQs.** As above.
3. **Phase 1 setup.** 3 models x 4 harnesses, 40 runs each over 34 tasks; run count set by subscription quotas
   and agreed with the instructor.
4. **Phase 1 result.** The harness matters for pass rate, cost and time: with the same model, GLM passed 4 to 16
   of 40 runs and Sonnet 10 to 16, depending on the harness.
5. **Cheap model, right harness.** The harness moves results more than the model does. GLM's spread across
   harnesses (4 to 16 of 40) is larger than the gap between GLM and Sonnet overall (43 vs 51 of 160). GLM equals
   Sonnet in Claude Code (16 vs 16) and beats it in ZCode (15 vs 10), at $0.63-0.99 per solve vs Sonnet's best
   $2.13. The trade-off is time: GLM runs took a median of 42-47 minutes there vs 10-11 for Sonnet.
6. **Splitting coupled tasks.** Our first design split each task into steps and routed each step (planner +
   Laya). Terminal-Bench tasks proved tightly coupled: steps share hidden state such as files and seeds, so
   splitting added coordination failures (a step run in parallel needed a seed another step had to find;
   planners produced straight chains with no parallelism; results passed between steps were truncated). That
   design solved 3 of 37 runs, and the task text alone did not reveal which tasks needed the strong model. This
   led us to route whole tasks after the cheap model's attempt.
7. **The method (v2.8).** GLM-5.3 Flash builds first; Opus 5.5 reviews once and takes over only if needed, so
   deferral is decided after the cheap attempt, not from the prompt.
8. **Main result.** On 18 evaluation tasks: 11/36 solved vs Opus alone 4/35, $3.80 vs $7.83 per solve, at 34%
   higher total cost.
9. **Provider finding (RQ3).** With GLM served by Z.ai instead of Factory, the cascade still led (4/18 vs 2/18)
   but by less; Opus alone was unchanged.
10. **Limits.** Compute budget (subscriptions, usage caps); small samples (1-2 task gaps are within run-to-run
    variance); commercial backends change and can end; cost is at list price, not what we paid.
11. **Phase 2 scope.** 30 Terminal-Bench 4.0 tasks: 12 for development, 18 for evaluation (2 runs each, then 1
    run each with GLM served by Z.ai). The 18 were part of an early baseline sweep that motivated v2.8's design;
    no setting was tuned on them afterwards.
12. **Reliability.** The harness decides whether a run finishes (GLM: 7 no-result runs of 40 under Hermes vs 0-3
    elsewhere), and single runs flip (6 of 24 groups), so 1-2 task gaps are treated as noise.
13. **Where the cascade's cost goes.** 94% of v2.8's cost is the Opus takeover, which explains the 34% higher
    total and points to the next design step (shorter takeovers).

## Contributions

1. **Harness study (RQ1, RQ1a-b).** Three models x four harnesses on Terminal-Bench 4.0 and SWE-Bench Pro.
2. **Splitting coupled tasks.** A finding about agentic tasks that motivated the cascade (claim 6).
3. **Cheap-first cascade with strong-model review (RQ2).** A learning-to-defer / model-cascade method
   (FrugalGPT, AutoMix) where deferral is decided after the cheap model's attempt.
4. **Serving-provider sensitivity (RQ3, exploratory).** GLM-5.3 Flash served by Factory (Droid subscription;
   Factory hosts GLM itself) vs Z.ai (GLM's own provider, GLM Coding Plan).

## Facts we present

Computed from the logs on main, 2026-11-21. Excluded hardware tasks (live-database-cutover, jax-speedrun-gpu)
are left out; a run with no result counts as not passed. GPT-6.1 Sol is added after Mahek's merge.

### Phase 1: 40 runs per configuration (34 tasks: 28 Terminal-Bench 4.0, 6 SWE-Bench Pro)

Plan (seed 570): 30 Terminal-Bench + 6 SWE-Bench Pro tasks, plus 2 extra runs on 3 tasks for variance = 42 runs;
2 Terminal-Bench tasks needing a GPU or more than 14 CPUs excluded for everyone = 40 runs per configuration.

| Model | Harness | Passed / 40 | Terminal-Bench / SWE | Cost | Cost per solve | Median minutes |
| --- | --- | --- | --- | --- | --- | --- |
| Sonnet 5.5 | Claude Code | 16 | 12 / 4 | $34.09 | $2.13 | 9.9 |
| Sonnet 5.5 | Hermes | 14 | 12 / 2 | $33.84 | $2.42 | 12.7 |
| Sonnet 5.5 | Codex CLI | 11 | 6 / 5 | $26.72 | $2.43 | 9.9 |
| Sonnet 5.5 | ZCode | 10 | 5 / 5 | $41.12 | $4.11 | 11.1 |
| GLM-5.3 Flash | Claude Code | 16 | 11 / 5 | $10.14 | $0.63 | 47.4 |
| GLM-5.3 Flash | ZCode | 15 | 11 / 4 | $14.89 | $0.99 | 41.7 |
| GLM-5.3 Flash | Hermes | 8 | 6 / 2 | $22.12 | $2.76 | 52.7 |
| GLM-5.3 Flash | Codex CLI | 4 | 1 / 3 | $10.88 | $2.72 | 24.0 |

- Totals: Sonnet 51/160, $135.76, $2.66 per solve. GLM 43/160, $58.02, $1.35 per solve.
- Runs with no result: GLM 13 (Hermes 7), Sonnet 0.
- Token counts are left out until cached-token accounting is checked across harnesses.

### Variance (3 tasks x 3 runs in every configuration)

Agents are not deterministic: the same model, harness and task can pass once and fail the next time.
P = pass, F = fail, - = no result (runs 1, 2, 3).

| Model / harness | batched-eval | data-anonymization | interleaved-vigenere |
| --- | --- | --- | --- |
| Sonnet, Claude Code | P F P | F F F | P P P |
| Sonnet, Codex | F P P | F F F | F F F |
| Sonnet, Hermes | P P P | F F F | F F P |
| Sonnet, ZCode | F P P | F F F | F F F |
| GLM, Claude Code | P P P | F F F | P P P |
| GLM, Codex | F F F | F F F | F F F |
| GLM, Hermes | F F F | - F F | P P - |
| GLM, ZCode | F P P | F F F | P F P |

6 of 24 groups flipped between pass and fail; 2 more changed only because a run gave no result. We treat 1-2
task gaps as possible noise and call a difference real only when it is larger.

### Router feasibility study (proposal, 24 author-labelled tasks, zero-shot)

Arch-Router rho 0.94, 24/24 tiers, 189 ms; Laya rho 0.87, 18/24, 8 ms; NVIDIA classifier rho 0.56, 12/24, 26 ms;
RouteLLM BERT rho -0.23, 4/24, 12 ms. These four were compared offline only. Inside the harness only Laya routed
steps; the Arch-Router fallback never fired in logged runs, and a fine-tuned Laya was not built.

### Phase 2: splitting and routing from task text

- v1 (planner + Laya per step): 3 of 37 runs solved vs Sonnet alone 5 of 22 and Opus alone 5 of 10.
- Coupling evidence (`docs/COORDINATION_DESIGN.md`): a step run in parallel needed a seed another step had to
  find and spent 95% of the budget; planners produced 4-step straight chains; results passed forward were cut to
  300 characters.
- Laya in the harness: 91 step decisions; score mean 1.16, SD 0.19 (scale 0-2); 77% to the middle tier;
  confidence mean 0.38, max 0.55; median 448 ms per decision.
- Offline (42 dev runs, leave-one-task-out): task-text features AUC 0.46-0.50; the best learned router saved
  money only by losing solves (18 solves, $22.10 vs 20 solves, $38.02 always cascading).

### Phase 2: the cascade (v2.8), 18 evaluation tasks (Terminal-Bench 4.0)

| Setup | GLM served by | Runs | Solved | Hidden tests passed | Total cost | Cost per solve |
| --- | --- | --- | --- | --- | --- | --- |
| v2.8 | Factory (Droid) | 36 | 11 (31%) | 75% | $41.83 | $3.80 |
| Opus 5.5 alone | Factory (Droid) | 35 | 4 (11%) | 68% | $31.31 | $7.83 |
| GLM-5.3 Flash alone | Factory (Droid) | 34 | 2 (6%) | 63% | $3.21 | $1.60 |
| v2.8 | Z.ai (via opencodex) | 18 | 4 (22%) | - | $23.22 | $5.80 |
| Opus 5.5 alone | Anthropic (via opencodex) | 18 | 2 (11%) | - | $18.70 | $9.35 |

- v2.8 vs Opus alone (Droid): 2.8x the solves, 51% lower cost per solve, 34% higher total cost. 94% of v2.8's cost
  is the Opus takeover; every held-out solve went through an Opus takeover.
- Dev control, same 12 tasks: v2.8 6/12 ($11.09) with Factory-served GLM vs 3/12 ($13.31) with Z.ai-served GLM;
  GLM output about 130 vs 59 tokens/s.
- Ablations that did not beat v2.8: v3.5 (prompt cache kept) 7/36 on held-out, $5.13 per solve; lower effort;
  Sonnet takeover; capped takeover; GLM fixing from Opus's list; v3.10 skip rule; v4 solve rate 0.40 vs 0.50;
  v4.1 2 of 6 vs 3 of 6.
- Task sets: 12 dev + 18 evaluation, no overlap; 9 of the 18 are also Phase 1 tasks. All 18 were in an early
  37-task baseline sweep (dt1, 2026-11-02) whose overall results motivated v2.8's design (commit fabb6c3); no
  setting was tuned on them afterwards and the evaluation used fresh runs. Say this plainly.

## Section outline

| # | Section | Main points | Writer | Pages |
| --- | --- | --- | --- | --- |
| 1 | Abstract | Thesis, RQ1-RQ3, contributions, headline numbers | Mahek (last) | ~200 words |
| 2 | Introduction | Claim 1, RQs, contributions | Mahek | ~1 |
| 3 | Related work | Agentic benchmarks (SWE-bench, Terminal-Bench, SWE-Bench Pro); harness effects (SWE-agent, OpenHands); routing and cascades (RouteLLM, FrugalGPT, AutoMix, Arch-Router, NVIDIA classifier); learning to defer; multi-agent coordination (MetaGPT) | Mahek | ~0.75 |
| 4 | Phase 1 method | Grid, task plan, exclusions, variance runs, clean-harness rules, pinned versions, list-price cost (RULES.md) | Ammar | ~0.75 |
| 5 | Phase 1 results | Table, Pareto figure, harness effect, RQ1a, RQ1b, reliability, variance | Ammar | ~1 |
| 6 | Phase 2 method | v1 design and why it was replaced (claim 6); v2.8; baselines in the same harness | drafted from repo docs; Ibrahim checks | ~1.25 |
| 7 | Phase 2 setup | 12 dev + 18 evaluation tasks, runs, providers, list prices | drafted; Ibrahim checks | ~0.5 |
| 8 | Phase 2 results | Splitting results, cascade table, provider replication, ablation table | drafted; Ibrahim checks | ~1.25 |
| 9 | Discussion | Why deferral after the attempt works; where the cost goes; harness and provider are part of the system; list price vs subscription | Ibrahim | ~0.75 |
| 10 | Limitations | Compute budget first, then small samples, backends that change, router baselines offline only | Ammar | ~0.5 |
| 11 | Conclusion | Thesis restated with the main numbers | Mahek | ~0.25 |
| 12 | Appendices | Per-task tables, prices, harness versions, rerun and void rules, judge and takeover prompts | Ammar (Phase 1), drafted (Phase 2) | - |

Main-text length is open; the group decides (8 pages is the usual conference limit, if we want that option).

## Wording rules

Group voice ("we", "our"), plain academic tone, short sentences, exact numbers, no promotional adjectives.

| Topic | Say | Do not say |
| --- | --- | --- |
| Run counts | "40 runs per configuration, a scope set by subscription quotas and agreed with the instructor" | "we only managed 40", "limited data" |
| Laya | "routing from task text did not separate tasks by required capability" | "Laya failed" |
| v1 design | "the decomposition design was evaluated and replaced by the cascade" | "our first idea didn't work" |
| v2.8 cost | "34% higher total cost than Opus alone, 51% lower cost per solved task" | "cheaper than Opus" |
| Providers | "the same model served by two providers (Factory via Droid; Z.ai)" | "opencodex is a provider" (it is our local tool that lets subscriptions be used from any harness); "Z.ai is worse" |
| Small differences | "within run-to-run variance" when the gap is 1-2 tasks | "clearly better" |
| Cost | "cost at public list prices" | "what we paid" |
| Evaluation tasks | "18 evaluation tasks, seen once in an early baseline sweep" | "never seen" |

Facts that are easy to get wrong:

- On Droid the evaluation ran 2 runs per task (out of 36); with Z.ai 1 run per task (out of 18). Compare rates.
- v2.8 is the final method; v3.x and v4.x are ablations in one table.
- Both backends served GLM-5.3 Flash (`droid/glm-5.3-flash`, `zai/glm-5.3-flash`), not GLM 5.3.
- Mahek's Sol costs change after the cached-price fix ($0 to $0.10 per million cached tokens, PR 15).

### Compute-budget limitation (suggested wording)

> All experiments ran on consumer subscription plans (Anthropic, OpenAI, Z.ai and Factory) rather than metered
> API access, which would have cost substantially more at the scale of this study. Subscription plans cap usage
> per 5-hour and weekly window, which bounded the number of runs per configuration (40), the number of repeated
> runs used to estimate variance (3 tasks x 3 runs), and the set of models and harnesses we could include.
> Larger samples, more repeats per task and additional models and harnesses would narrow the uncertainty in our
> comparisons.

## Open items

- [ ] Mahek: one PR with her full Sol column (136 first runs from `phase1/mahek-reruns` + 24 variance runs from
      `phase1/mahek-variance` = 160), then `scripts/recompute_cost.py` for the $0.10 cached price.
- [ ] After Mahek's PR: compute Phase 1 tables and the Pareto figure from all three columns (script, not by hand).
- [ ] Group: agree the page limit.
- [ ] Group: release the repo publicly, or say "available on request" in the paper.
