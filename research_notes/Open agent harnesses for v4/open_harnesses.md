# Open-source coding/terminal agent harnesses: what scores well, and what v4 can borrow

Research date: 2026-11-18. Every number below is copied from the cited page as fetched on that date. Third-party leaderboards (Vals, Snorkel) are aggregators; the official tbench.ai leaderboard rendered no rows to the fetcher (dynamic page), so no number here comes from tbench.ai directly.

## Comparison table of harnesses (licence, architecture, tools, context, planning, sub-agents, models)

### Takeaway
Only mini-swe-agent, Terminus 2, OpenHands, Hermes and SWE-agent have primary-source design details in this research; the CLI agents (OpenCode, Goose, Qwen Code, Kimi CLI, Gemini CLI, Codex, Aider) are mostly confirmed only for licence and Harbor support. Almost every listed harness is pre-integrated in Harbor, so swapping the worker is a config change, not a port.

### Cited Findings
- **mini-swe-agent**: MIT; "does not have any tools other than bash", does not use the LM tool-calling interface; every action is an independent `subprocess.run` (no persistent shell); linear history (trajectory == prompt); agent class is "some 100 lines of python"; models via litellm/OpenRouter/Portkey; envs local, docker/podman, singularity, bubblewrap, contree; README claims ">74%" SWE-bench Verified (no model/date given); current branch is "v2" — [GitHub SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)
- **Terminus 2** (Terminal-Bench authors' reference agent): single tool, a headless terminal; LM emits keystrokes executed in a tmux shell inside the Docker container; no dedicated edit/run/download tools; can scroll, use arrow keys, open extra shells; when the context limit is hit, a separate LM call condenses the context ("extended tasks that may need millions of tokens"); trace examples are JSON with `keystrokes`, `duration`, `task_complete` fields — [Terminal-Bench 2.0 paper, arXiv 2601.11868, App. F / C.3, v1 17 Jan 2026](https://arxiv.org/html/2601.11868)
- Vals notes Terminus 2 "does not use structured outputs to enforce a response schema"; invalid/missing JSON is retried with a warning — [Vals Terminal-Bench 2.0](https://www.vals.ai/benchmarks/terminal-bench-2)
- **SWE-agent** (2024): custom Agent-Computer Interface — summarized search, 100-line file viewer, edit command with linting guardrail, history collapsed to last 5 observations, demonstration in prompt — [SWE-agent paper, arXiv 2405.15793](https://arxiv.org/html/2405.15793v3)
- **OpenHands**: MIT except the `enterprise/` directory (separate licence); rebuilt around a composable Agent SDK — [DEV review](https://dev.to/jovan_chan_9500711396d4e6/openhands-review-2026-the-76k-star-coding-agent-5pa) (secondary). Context condenser summarizes older turns past a threshold, keeping goals, progress, remaining work, critical files and failing tests — [OpenHands blog, 9 Apr 2025](https://openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents)
- **Hermes Agent** (Nous Research): MIT; plan → tool call → observe loop; six terminal backends (local, Docker, SSH, Daytona, Singularity, Modal); 60+ built-in tools plus MCP; concurrent subagents; "closed learning loop" — [Hermes site](https://hermes-agent.nousresearch.com/ai-agent-platform), [Arize analysis](https://arize.com/blog/how-hermes-implements-open-source-agent-harness-architecture/). Release dates conflict (Feb 2026 vs v0.11.0 Apr 2026). Nous has published no Terminal-Bench score for Hermes; the often-quoted 52.8→66.5 is LangChain's, not Hermes's — [Capital & Compute](https://capitalandcompute.net/blog/hermes-agent-explained/)
- **Licences (secondary comparison sites)**: OpenCode MIT; Pi MIT; Aider, Goose, Cline, Codex, Qwen Code Apache-2.0 (one site says Qwen Code MIT — conflict); Gemini CLI Apache-2.0; Kimi CLI licence not found — [pinggy.io](https://pinggy.io/amp/blog/best_open_source_cli_coding_agents), [toolquestor](https://toolquestor.com/vs/qwen-code-vs-opencode)
- Subagents: Goose, OpenCode (Build/Plan agents + subagents) and Qwen Code listed as having subagents — [pinggy.io](https://pinggy.io/amp/blog/best_open_source_cli_coding_agents)
- Gemini CLI stops serving Google AI Pro/Ultra and free individual users on 18 June 2026; succeeded by Antigravity CLI. Goose moved to the Linux Foundation — [pinggy.io](https://pinggy.io/amp/blog/best_open_source_cli_coding_agents) (secondary)
- **Harbor pre-integrated agents** (exact names): `aider`, `antigravity-cli`, `antigravity-sdk`, `claude-code`, `cline-cli`, `codex`, `computer-1`, `copilot-cli`, `cortex-code`, `cursor-cli`, `deerflow`, `devin`, `dspy-rlm`, `eve`, `fx`, `gemini-cli`, `goose`, `grok-build`, `hermes`, `junie`, `kimi-code`, `kimi-cli`, `langgraph`, `mcode`, `mimo`, `mini-swe-agent`, `muse-code`, `nemo-agent`, `openclaw`, `opencode`, `openhands`, `openhands-sdk`, `oracle`, `nop`, `pi`, `qwen-coder`, `rovodev-cli`, `strands`, `swe-agent`, `terminus-2`, `trae-agent`, `vibe`, `fabric`, `opencode-v2`. `skip_install=true` reuses an agent already baked into the task image — [Harbor docs](https://docs.harborframework.com/agents/pre-integrated-agents)

### Inferences
- Tool-set and todo-list features for OpenCode, Goose, Qwen Code, Kimi CLI, Codex CLI and Aider were not confirmed from primary sources in this pass. From training knowledge (Unverified, pre-June 2026): OpenCode, Qwen Code, Codex CLI, Kimi CLI and Gemini CLI ship dedicated read/edit/grep/glob tools plus a shell and a todo/plan tool; Aider uses diff-format edits with a repo map and no shell-agent loop. Check each repo before citing.
- Design split that matters for v4: raw-shell minimalists (mini-swe-agent, Terminus 2) vs tool-rich CLIs (OpenCode, OpenHands, Codex). Neither side wins uniformly (see next section).

### Gaps
- No primary-source per-harness feature matrix for todo tools; no Kimi CLI licence; no official tbench.ai rows (page is JS-rendered).

## Which harness design choices have measured effects on pass rate or cost?

### Takeaway
The only clean component ablations are old (SWE-agent 2024: linting editor +3.0, 100-line viewer, history truncation +3.0 points on 18.0%). Newer evidence is whole-harness swaps (spreads of 2–17 points at a fixed model) and one vendor case study where verification-forcing middleware was part of a +13.7-point gain. Self-judging "done" is the most documented failure.

### Cited Findings
- SWE-agent ACI ablation, SWE-bench Lite, GPT-4 Turbo, full config 18.0%: edit without linting 15.0 (−3.0); no edit command 10.3 (−7.7); iterative search 12.0 (−6.0); no search 15.7 (−2.3); viewer 30 lines 14.3 (−3.7); full-file viewer 12.7 (−5.3); full history instead of last-5 observations 15.0 (−3.0); no demonstration 16.3 (−1.7); shell-only agent 11.00% — [SWE-agent paper, arXiv 2405.15793](https://arxiv.org/html/2405.15793v3)
- Same-model harness swaps on Terminal-Bench 2.0 (Table 2): Claude Opus 4.5 — Terminus 2 57.8%, Claude Code 52.1%, OpenHands 51.9%; GPT-5 — Codex CLI 49.6%, OpenHands 41.5%, Terminus 2 35.2%, Mini-SWE-Agent 33.9%; GPT-5-Mini — Codex CLI 31.9%, OpenHands 27.7%, Terminus 2 24.0%, Mini-SWE-Agent 22.2%; Claude Sonnet 4.5 — Terminus 2 42.8%, Mini-SWE-Agent 42.5%, OpenHands 40.3%, Claude Code 40.1%; Gemini 2.5 Pro — Terminus 2 32.6%, Mini-SWE-Agent 26.1%, Gemini CLI 19.6%, OpenHands 15.7%; GPT-5.2 — Codex CLI 62.9%, Terminus 2 54.0%. Paper's conclusion: model choice generally matters more than scaffold — [arXiv 2601.11868](https://arxiv.org/html/2601.11868)
- Terminal-Bench 2.0 command-level errors (Terminus 2 trajectories): "executables not installed or not in PATH" is 24.1% of all command failures, the largest category; command error rate ranges 9.2% (Grok 4) to 26.7% (GPT-OSS-120B). Trajectory failure taxonomy includes Premature Termination, No or Irrelevant Verification, Weak Verification, Context Loss, Step Repetition, Unaware of Termination Conditions — [arXiv 2601.11868](https://arxiv.org/html/2601.11868)
- LangChain deepagents-cli, gpt-5.2-codex fixed, Terminal-Bench 2.0: 52.8% → 66.5% (+13.7) from harness changes only (17 Feb 2026). Changes: build-verify prompting, PreCompletionChecklistMiddleware (intercepts exit, forces verification against the task spec), LoopDetectionMiddleware (after N edits to the same file, prompt to rethink), LocalContextMiddleware (maps workdir and available tools at start), time-budget warnings. Only one isolated number: xhigh reasoning everywhere 53.9% (timeouts) vs high 63.6%; xhigh-high-xhigh "sandwich" 66.5%. Most common failure: agent writes a solution, re-reads its own code, judges it fine, stops; "Models are biased towards their first plausible solution" — [LangChain blog](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering) (vendor self-report, no independent replication found)
- LLM judges of completion: "false success" is 75.8% of failures on AppWorld for architectures with explicit completion signals; none of 5 LLM-judge configurations worked reliably; judges read longer action sequences as success; TF-IDF detectors reach task-disjoint AUROC 0.83 (tau2-bench) / 0.95 (AppWorld) and catch 4–8x more false successes than the best judge at the same flag rate — [Advani, arXiv 2606.09863, June 2026](https://arxiv.org/abs/2606.09863) (single preprint)
- Context condensation: OpenHands condenser 54% vs 53% baseline solved on a SWE-bench Verified subset, per-turn cost "less than half" baseline ("up to 2x" reduction) — [OpenHands blog, 9 Apr 2025](https://openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents)
- Marin ablation (24 Sep 2026), SWE-bench Verified 100-task subset, mean reward: Qwen 3.6 35B A3B — terminus-2 0.640, pi 0.660, opencode 0.620; Gemma 4 26B A4B — 0.540 / 0.530 / 0.474; Nemotron Lightning 3.5 — 0.470 / 0.533 / 0.586. terminus-2 had 49 AgentTimeoutErrors of 669 trials; opencode 11 ContextLengthExceeded. Confound: pi capped at 73,728 context / 8,192 output — [marin issue #9411](https://github.com/marin-community/marin/issues/9411)
- Survey claim: leaderboard gaps between harnesses are substantially larger than reported standard errors, but the benchmark does not support a harness-only interpretation — [arXiv 2606.20683](https://arxiv.org/pdf/2606.20683) (search snippet only, not fetched)

### Inferences
- Our "ship verdict wrong 0/8" matches the literature: LLM judges reading a trajectory are unreliable; the fix in every source is executable verification (run the task's checks/tests) rather than a stronger judge.
- 24.1% of command failures being missing executables supports pre-baking dependencies (Harbor `skip_install` / prepared images, or a cached setup step) and a LocalContext-style environment probe at start; it also explains reinstall churn.
- Stale handoff context: the SWE-agent "last 5 observations" result and the OpenHands condenser (structured summary: goal, progress, remaining, files, failing tests) suggest the handoff should be a fixed-schema state summary, not raw transcript.
- Raw shell is not clearly worse on Terminal-Bench: Terminus 2 (keystrokes only) beat Claude Code and OpenHands with Claude models. Tool richness helped on SWE-bench Lite in 2024 (shell-only 11.00 vs 18.0). The edit-with-lint guardrail is the most transferable single tool.

### Gaps
- No 2026 primary-source ablation isolating todo lists or sub-agents on Terminal-Bench/SWE-bench was found.
- Terminal-Bench 2.0 trajectory-failure percentages are in figures not extractable from the HTML.

## Which harnesses are Harbor-compatible or easy to embed as the worker instead of mini-swe-agent?

### Takeaway
All main candidates (terminus-2, openhands, openhands-sdk, opencode, opencode-v2, goose, hermes, qwen-coder, kimi-cli, codex, pi, swe-agent, aider) are pre-integrated Harbor agents. Terminus 2 is the natural drop-in: it is Harbor's own reference agent, runs in the task container via tmux, and has built-in context summarization.

### Cited Findings
- Harbor pre-integrated list includes `terminus-2`, `mini-swe-agent`, `openhands`, `openhands-sdk`, `opencode`, `opencode-v2`, `goose`, `hermes`, `qwen-coder`, `kimi-cli`, `kimi-code`, `codex`, `pi`, `swe-agent`, `aider`; `skip_install=true` skips installation when the image already has the agent; for `claude-code`, `codex`, `opencode`, `opencode-v2` Harbor reuses the image's installed version — [Harbor docs](https://docs.harborframework.com/agents/pre-integrated-agents)
- mini-swe-agent swaps `subprocess.run` for `docker exec` to sandbox; no persistent shell (deliberate design choice) — [mini-swe-agent README](https://github.com/SWE-agent/mini-swe-agent)

### Inferences
- Embedding a pre-integrated agent per DAG step from inside our own `RoutedHarness` would need us to call its installed-agent runner per step; Terminus 2 and mini-swe-agent are the only two that are small Python loops we can call in-process with our own model endpoint. Unverified until we read Harbor's terminus-2 source.
- Persistent shell (Terminus 2 tmux) keeps env vars, venvs and `cd` between steps; mini-swe-agent's stateless subprocess loses them, which plausibly contributes to reinstalls.

### Gaps
- Did not read Harbor's custom-agent page or terminus-2 source; whether terminus-2 can be invoked as a library per step is unconfirmed.

## What harness gives the best pass rate with a cheap open model on Terminal-Bench 2.x?

### Takeaway
No open-model + non-Terminus harness result with a primary source was found. Best documented cheap-open numbers are Terminus 2 runs on Vals (snapshot 4 June 2026): Kimi K2.6 57.30% at $0.19/task, DeepSeek V4 56.18% at $0.18, GLM 5.1 53.93% at $0.16. GLM 5.3 Flash has no Terminal-Bench 2.0 entry; on SWE-bench Verified (mini-swe-agent) it scores 92.00% at $0.02/task.

### Cited Findings
- Vals Terminal-Bench 2.0, Terminus 2, pass@1, updated 6/4/2026, archived ("performance on this benchmark has saturated"): Qwen 3.7 Max 59.18% ($0.72), Kimi K2.6 57.30% ($0.19), DeepSeek V4 56.18% ($0.18), GLM 5.1 53.93% ($0.16), Qwen 3.6 Max Preview 51.69% ($0.32), GLM 5 49.44% ($0.25), MiniMax-M2.7 47.19% ($0.07), MiniMax-M3 46.07% ($0.22), Qwen 3.6 27B 44.94% ($0.30). For reference GPT 5.5 73.20% ($0.76), Claude Opus 4.8 70.04% ($0.88) — [Vals](https://www.vals.ai/benchmarks/terminal-bench-2)
- Vals SWE-bench Verified, mini-swe-agent, updated 9/1/2026, archived: GLM 5.3 95.40% ($0.34), GLM 5.3 Flash 92.00% ($0.02), DeepSeek V4 Pro 0813 96.40% ($0.10), DeepSeek V4 Flash 0731 88.80% ($0.01), Kimi K3 93.40% ($0.76), Qwen 3.8 27B 86.00% ($1.08); top Claude Opus 5 97.00% ($1.29) — [Vals SWE-bench](https://www.vals.ai/benchmarks/swebench)
- Snorkel Terminal-Bench 2.0 top 10 (142 entries; no row dates): NexAU-AHE + GPT-5.5 84.7% (±2.1), LemonHarness "Multiple" 84.5% (±2.6), Capy + GPT-5.5 83.1% (±2.1), Codex CLI + GPT-5.5 82.2% (±2.2), Polaris "Multiple" 82.2%, WOZCODE + Claude Opus 4.7 80.2%, TongAgents + Gemini 3.1 Pro 80.2%, SageAgent + GPT-5.3-Codex 78.4%, Droid + GPT-5.3-Codex 77.3%. No open-weight model and none of Terminus 2 / mini-swe-agent / OpenHands / OpenCode / Goose / Hermes in the top 10 — [Snorkel](https://snorkel.ai/leaderboard/terminal-bench-2-0/)
- Other reported open-model numbers with unstated or different harnesses: BenchLM GLM-5 56.2%, Kimi K2.6 66.7% (harness not stated); DataLearner DeepSeek-V4-Pro 67.90 (extra-high thinking) — [search summary of BenchLM/DataLearner](https://benchlm.ai/benchmarks/terminal-bench-2) (not fetched; treat as Unverified)
- Tmax (Ai2 et al., v2 2 Nov 2026): a 9B open model trained with RL on synthetic terminal environments reaches 33% on Terminal-Bench 2.0 — [arXiv 2606.23321](https://arxiv.org/abs/2606.23321)
- Tbench.ai shows Terminal-Bench 4.1 as its current headline table (columns Rank, Model, Agent, Resolution Rate, Cost, Tokens) — [tbench.ai](https://www.tbench.ai/leaderboard/terminal-bench/2.0) (rows not rendered)

### Inferences
- SWE-bench Verified is saturated for our worker (GLM 5.3 Flash 92.00% bash-only), so the worker's weakness on our tasks is likely terminal-environment work (deps, PATH, long-running processes), not code editing — consistent with the 24.1% missing-executable failure share.
- Cheapest strong design evidence points to: Terminus-2-style persistent tmux shell + summarizing context + forced executable verification before exit + environment probe/pre-baked deps. These are worker-side changes compatible with keeping the Opus planner.

### Gaps
- No primary-source Terminal-Bench 2.x result for GLM 5.3 / 5.3 Flash under any harness.
- No source compares OpenCode/Goose/Hermes vs Terminus 2 with the same open model on Terminal-Bench 2.x (Marin compared terminus-2/pi/opencode only on SWE-bench Verified subset).

## Change Record
Task: Open harness research for v4
Linear: None
Update: Sourced comparison of open harnesses, harness ablations, Harbor compatibility and cheap-open-model Terminal-Bench numbers.
Evidence: Source inspected (web, 2026-11-18)
Next: Read Harbor terminus-2 source to confirm it can run per DAG step with GLM 5.3 Flash via opencodex; then prototype verify-before-exit middleware.
Edited by: Claude Code (Anthropic, Claude Opus 5.5) — 2026-11-18T18:32:12+0400
