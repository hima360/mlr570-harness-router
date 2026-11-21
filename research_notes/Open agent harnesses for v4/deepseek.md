# DeepSeek agent harness: fact check (as of 2026-11-18)

Evidence key: "Verified live 2026-11-18" = read from GitHub API / primary page today. "Secondary, unverified" = blog or search summary not checked against a primary source.

## Does DeepSeek publish an agent harness, and is it open source?

### Takeaway
Yes. DeepSeek publishes **DeepSeek Harness (`dsh`)**, an MIT-licensed TypeScript agent harness at github.com/deepseek-ai/deepseek-harness, created 2026-08-13, still a "developer preview" with release candidates only (latest dsh-v0.2.2-rc.5, 2026-11-18). "Very good" is partly true: on the only independent same-model comparison found, it ties for first on most views but ranks lower on Terminal-Bench because of a background-process cleanup behaviour.

### Cited Findings
- Repo `deepseek-ai/deepseek-harness`: created 2026-08-13, last push 2026-11-18, 283,477 stars, 35,162 forks, TypeScript, MIT licence, homepage deepseek.com/harness (Verified live 2026-11-18 via GitHub API) — [GitHub](https://github.com/deepseek-ai/deepseek-harness)
- Last commit 2e39e704, 2026-11-18 ("sync release dsh-0.2.2"); releases are all release candidates: rc.1 2026-11-04 through rc.5 2026-11-18 (Verified live) — [GitHub releases](https://github.com/deepseek-ai/deepseek-harness/releases)
- README: "open-source agent harness developed by DeepSeek AI", "everything-is-a-plugin" architecture on the Cordis plugin framework; "developer preview ... THERE WILL BE COMPATIBILITY-BREAKING CHANGES"; run with `npx @deepseek-ai/dsh web`; feedback via GitHub Discussions (Verified live) — [README](https://github.com/deepseek-ai/deepseek-harness/blob/master/README.md)
- Official product page: "public preview worldwide", desktop app (macOS Apple silicon, Windows), Web UI; experimental: agent teams, auto-approval reviewer, scheduled tasks, voice input. No benchmark numbers on the page — [deepseek.com/en/harness](https://deepseek.com/en/harness/)
- Second related repo: `deepseek-ai/dsh-superagent-prototype` (MIT, 23,788 stars, single commit 2026-10-31). README says it is a "research prototype for reference only, not an official DeepSeek product": a proactive GitHub/Feishu-driven coding agent packaged as a dsh bundle (Verified live) — [GitHub](https://github.com/deepseek-ai/dsh-superagent-prototype)
- Other agent-adjacent DeepSeek org repos are lists, not harnesses: `awesome-deepseek-agent` (6,455 stars, no licence), `awesome-deepseek-integration` (CC0). The rest of the org is models and infra kernels (DeepEP, FlashMLA, 3FS, DeepInfer) (Verified live, org repo listing) — [deepseek-ai org](https://github.com/deepseek-ai)
- Star counts in blogs differ (about 95k in earlier articles vs 283k now); figures are time-sensitive (Secondary) — [flowtivity.ai](https://flowtivity.ai/blog/deepseek-harness-open-source-agent-explained/)
- A third-party project also named "deepseek-harness" exists; only the deepseek-ai org repo is official (Secondary, unverified) — [antigravityskills.com](https://antigravityskills.com/docs/deepseek-harness)

### Inferences
- The "open source" claim holds (MIT). The "mature" reading does not: every release is an RC and the README warns of breaking changes, so pin a version for any experiment.
- Models with "agent modes" (V4, V4.1-Flash API) are separate from the harness; DeepSeek's harness is the only first-party harness product found.

### Gaps
- Whether external PRs are accepted: one blog says no; I did not read CONTRIBUTING.md.
- The "Exploring Super-agent Design" article referenced by the superagent README was not located.

## Architecture: models, tools, context handling, planning

### Takeaway
A single agent loop over a plugin kernel (Cordis); every component (model adapter, tool registry, session log, loop) is a swappable plugin. Multi-provider via a pi-ai adapter (OpenAI, Anthropic, OpenAI-compatible gateways), so it can run non-DeepSeek models. Has subagents, plan mode, goals, compaction and a minimal "bash + read_image" profile used for DeepSeek's own benchmark runs.

### Cited Findings
- Core packages: append-only `SessionEvent` log, system-prompt assembly, scoped tool registry with guarded pipeline (`tools/pre-execute -> execute -> post-execute` waterfall hooks), `core/agent-loop` default driver, `llm` adapter seam. "Every part of the product is a plugin, including the model adapter, the tool registry, the session log, and the agent loop itself" (Source inspected 2026-11-18) — [docs/architecture.md](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)
- "Model-visible means logged": every model request must be reconstructable from the session log; fork/resume/telemetry derive from it (Source inspected) — [docs/architecture.md](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)
- Model selection is snapshotted per step (`AssembleContext.modelSelection`, `agent/pre-step`), so the model can in principle change between steps of one session (Source inspected) — [docs/architecture.md](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)
- Providers: `dsh-llm-deepseek` (direct) and `dsh-llm-pi-ai`, which "routes model requests to multiple pi-ai providers, OpenAI-compatible gateways, or self-hosted servers from one configuration"; example config shows `openai` and `anthropic` routes with `apiKeyEnv` references; per-route retry policy (default five retries) (Source inspected) — [llm-pi-ai README](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-pi-ai/README.md)
- Packages include `subagent`, `team`, `plan`, `goal`, `todo`, `workflow`, `sandbox`, `guard`, `mcp`, `lsp`, `skill`, `browser-use`, `computer-use`, `compaction`, `ptc-runtime` (Source inspected, directory listing) — [packages/](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages)
- Tool catalogue includes `bash` (and persistent bash), `pwsh`, `str_replace_editor`, `apply_patch`, `edit`, `read`, `write`, `glob`, `grep`, terminal sessions (`terminal_open/read/send/signal`), `exit_plan_mode`, `create_goal/update_goal`, `ralph`, `run_code` (programmatic tool calling), `lsp`, `ask_user_question`, schedule tools (Source inspected) — [docs/tool-catalog.md](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/tool-catalog.md)
- Compaction: automatic summary of older history as token pressure builds, `/compact` on demand, a tool-result pruner that trims oversized outputs first, and image offload (Source inspected) — [packages/compaction README](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/compaction/README.md)
- `sdk-minimal` profile: one persistent shell (Bash on Unix, 300 s timeout) plus `read_image`, automatic compaction, JSONL sessions, no file tools/skills/subagents, "danger-full-access" policy for isolated workspaces; model chosen in the SDK init request; `DSH_SYSTEM_PROMPT` replaces the persona (Source inspected) — [sdk-minimal README](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/bundle/sdk-minimal/README.md)
- BENCHMARK.md: benchmarks are run via the Python SDK's `jsonrpc-agent` minimal variant, separate workspace and session per task (Source inspected) — [BENCHMARK.md](https://github.com/deepseek-ai/deepseek-harness/blob/master/BENCHMARK.md)
- dsh decides when to compact from the model's longest-reply setting; on a model whose max reply equals its context window, normal auto-compaction is disabled and many long trials ended with a full window (independent observation) — [HF Harness Leaderboard CHANGELOG](https://huggingface.co/spaces/joelniklaus/harness-leaderboard/blob/main/CHANGELOG.md)
- dsh fires a short side model call about 1 ms after the main call (independent observation) — [HF Harness Leaderboard CHANGELOG](https://huggingface.co/spaces/joelniklaus/harness-leaderboard/blob/main/CHANGELOG.md)

### Inferences
- It is a single-model-per-step loop, not a planner/executor multi-model router. Per-step model selection plus pluggable `agent/pre-step` hooks mean a builder->takeover switch could be written as a dsh plugin, but nothing found ships that policy.
- The benchmark configuration DeepSeek uses ("minimal mode") is essentially bash-only, close to mini-swe-agent / Claude Code `--bare`.

### Gaps
- I did not read the `subagent`, `team` or `plan` package internals; how subagents choose models is unverified.

## Reported benchmark results and independent reproduction

### Takeaway
DeepSeek reports model scores, not harness scores, and labels the harness inconsistently (dsh minimal mode for some releases, Claude Code / Mini-SWE-V2 for V4.1-Flash-1017). The one independent same-model test (HF Harness Leaderboard, Harbor, Terminal-Bench 2.1 + DeepSWE) puts dsh statistically tied for first in most views, but weaker on Terminal-Bench because it kills background services on exit.

### Cited Findings
- DeepSeek API changelog (Verified live 2026-11-18) — [api-docs.deepseek.com/updates](https://api-docs.deepseek.com/updates/):
  - 2026-07-31 V4-Flash: Terminal-Bench 2.1 82.7, DeepSWE 54.4; tested with "DeepSeek Harness minimal mode" (then "to be released soon"), max effort, top-p 0.95, T=1.0.
  - 2026-08-13 V4-Pro GA: Terminal-Bench 2.1 87.9, DeepSWE 62.7; harness not stated.
  - 2026-08-21 V4-Flash-Vision-Exp: Terminal-Bench 2.1 83.9; "DeepSeek Harness minimal mode".
  - 2026-09-10 V4.1-Flash: Terminal-Bench 2.1 90.6, TB 3.0 30.0, TB 4.0 31.2, DeepSWE 74.2; harness not stated.
  - 2026-10-17 V4.1-Flash-1017: TB 4.0 42.3 "with the Claude Code framework", DeepSWE 77.0 with Mini-SWE-V2; recommends DeepSeek Harness 0.2.1-rc.1+ for the best experience.
  - No 2026 entry reports SWE-bench Verified.
- DeepSeek-V4 tech report (April 2026) reportedly evaluated code agents in an "internally developed evaluation framework" with only a bash tool and a file-edit tool; SWE-bench Verified V4-Pro 80.6, V4-Flash 79.0; Terminal-Bench 2.0 V4-Pro-Max 67.9 (Secondary: search summary of the paper and HF blog, not fetched) — [arXiv 2606.19348](https://arxiv.org/html/2606.19348v1); [HF blog](https://github.com/huggingface/blog/blob/main/deepseekv4.md)
- Vals AI lists V4.1-Flash-1017 at 31.82% on Terminal-Bench 4.0 vs DeepSeek's 42.3 (Secondary, unverified) — [testmuai.com](https://www.testmuai.com/blog/deepseek-v4-1-flash/)
- Claim that V4-Pro scored 87.9 under dsh but 54.68 under Terminus 2 in a third-party rerun (Secondary, unverified; not traced to a primary) — [findharness.com](https://findharness.com/blog/why-deepseek-harness-benchmark-scores-differ)
- HF Harness Leaderboard (Joël Niklaus, launched 2026-11-05): fixed models, swapped harnesses; Terminal-Bench 2.1 + DeepSWE v1.1 via Harbor; 3 attempts per task; ties when the 95% bootstrap CI of the paired difference covers 0; cheating counts as fail — [README](https://huggingface.co/spaces/joelniklaus/harness-leaderboard/blob/main/README.md)
- dsh joined 2026-11-09 at version 0.2.2-rc.1, via a custom Chat Completions provider pointed at the board's proxy; DeepSeek web search disabled (needs a DeepSeek key) — [CHANGELOG](https://huggingface.co/spaces/joelniklaus/harness-leaderboard/blob/main/CHANGELOG.md)
- dsh macro scores after re-runs: GLM-5.3-Flash low 57.1, GLM-5.3-Flash high 63.5, DeepSeek-V4.1-Flash low 69.4. On GLM-5.3-Flash high it is "5th of 10, statistically tied with the leader", and "over all models at high effort it is 2nd" (2026-11-11 entry). For comparison in the same tables: Pi GLM high 65.9 / DS high 66.3; OpenCode DS high 69.8; Kimi Code GLM high 62.1 — [CHANGELOG](https://huggingface.co/spaces/joelniklaus/harness-leaderboard/blob/main/CHANGELOG.md)
- 20 dsh passes reclassified as fails for cheating (fetching upstream OCaml source; downloading Terminal-Bench reference solutions/tests) — [CHANGELOG](https://huggingface.co/spaces/joelniklaus/harness-leaderboard/blob/main/CHANGELOG.md)
- "In the one-task mode used here, its own process manager removes background processes when the session ends", so services can vanish before the grader calls them; recorded as the harness's own behaviour, scores unchanged (2026-11-10) — [CHANGELOG](https://huggingface.co/spaces/joelniklaus/harness-leaderboard/blob/main/CHANGELOG.md)
- The New Stack (2026-11-13): dsh tied for first overall but 6th on Terminal-Bench; passed 2 of 36 attempts on certain server tasks where others passed 36/36 (Secondary; original article returned 403, read via a summary) — [devopschat summary](https://www.devopschat.co/articles/deepseeks-coding-agent-is-too-tidy-for-its-own-good-and-benchmarks-are-punishing-it); [The New Stack](https://thenewstack.io/deepseek-harness-cleanup-benchmark/)

### Inferences
- DeepSeek's headline Terminal-Bench numbers are self-reported and mix harnesses across releases; they are not evidence that dsh beats other harnesses. The HF board is the only same-model, same-tasks comparison, and it shows dsh is competitive (in the tied top group), not dominant.
- Harness choice alone moves scores a lot on that board (the average spread is 27.3% OpenClaw to 51.4% mini-swe-agent at launch, per a secondary summary), so cross-paper comparisons of model numbers under different harnesses are not meaningful.

### Gaps
- No dsh result on DeepSeek-V4.1-Flash high or Flint was extracted from the board (values not in the changelog lines read); open the Space for full tables.
- No independent SWE-bench Verified reproduction for dsh found.

## Comparison with other open harnesses, and what our project can borrow

### Takeaway
dsh is a heavy, plugin-everything product harness; on Harbor-style benchmarks it performs in the same tied band as mini-swe-agent, Pi, OpenCode, Kimi Code and Hermes. For a GLM-5.3-Flash builder + Opus 5.5 takeover harness in Harbor, the borrowable ideas are small and specific, not the framework itself.

### Cited Findings
- Harnesses on the HF board: Claude Code, Codex, Pi, OpenCode, Hermes Agent, mini-swe-agent, Kimi Code, Qwen Code, OpenClaw, DeepSeek Harness (plus Claude Code minimal, Grok Build, Mistral Vibe, oh-my-pi in preparation) — [README](https://huggingface.co/spaces/joelniklaus/harness-leaderboard/blob/main/README.md); [CHANGELOG](https://huggingface.co/spaces/joelniklaus/harness-leaderboard/blob/main/CHANGELOG.md)
- At launch the best harness depended on the model: mini-swe-agent best on GLM-5.3-Flash, Hermes on DeepSeek-V4.1-Flash, Kimi Code on Flint (Secondary summary) — [aicoder.com](https://aicoder.com/news/news-20261105-joel-niklaus-hf-harness-leaderboard-28849-trials-mini-swe-agent-51-4); [toknow.ai](https://toknow.ai/posts/harness-leaderboard-same-model-nine-coding-agents/)
- dsh assumes unknown models are text-only (no images) unless told; context window and max reply must be configured per model — [CHANGELOG](https://huggingface.co/spaces/joelniklaus/harness-leaderboard/blob/main/CHANGELOG.md)

### Inferences (borrowable for our Phase 2 harness)
- **Log-is-truth sessions**: dsh's "every model request reconstructable from the log" rule fits our need to price each step and replay a takeover; record every request (model, tokens) as an event.
- **Tool-result pruning before summarising**: trim oversized tool outputs first, then compact; cheap and relevant for Flash-tier context use.
- **Minimal benchmark profile**: DeepSeek benchmarks with a bash-only "minimal mode", consistent with our mini-swe-agent executor; no evidence that the full tool set helps on Terminal-Bench.
- **Per-step model snapshot**: dsh freezes model selection per step and keeps it across retries; mirror that so an Opus takeover starts on a clean step boundary.
- **Avoid its failure mode**: do not kill background processes at the end of the agent run; Terminal-Bench graders call services after the agent exits (cost dsh 2/36 vs 36/36 on such tasks).
- **Set model limits explicitly** (context window, max output, image input) for GLM-5.3-Flash; dsh's compaction trigger broke when max reply equalled context window.
- **Guard against benchmark leakage**: dsh's web fetch let agents download reference solutions; disable web access in our Harbor runs or score it as a fail.
- Not worth adopting wholesale: TypeScript/Cordis stack, RC-only releases, and no built-in multi-model escalation; our Python Harbor agent would gain little from wrapping it.

### Gaps
- No source compares dsh's token cost per task against other harnesses; the board shows cost but values for dsh were not extracted.
