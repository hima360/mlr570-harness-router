# Agent UI for the Phase 2 harness (Codex/ChatGPT/Hermes look)

Scope: open-source frontends that can show a Python agent's plan, steps, active model, live cost and result in a chat-style app, and the effort to wrap `phase2.agent:RoutedHarness` in about 1-2 weeks. Repo metadata (licence SPDX, stars, last push) pulled from the GitHub API on 2026-11-18 (Automated check). Effort numbers are my estimates, not sourced.

Local fact (Source inspected, 2026-11-18): every harness event goes through one method, `Trace.log(event, **data)` in `/Users/hima/Developer/mlr570-harness-router/phase2/trace.py`, which appends a JSONL row `{"t", "event", ...}`. Event names in use include `plan`, `route`, `llm_call`, `llm_call_failed`, `step_end`, `escalate`, `takeover`, `handoff`, `senior_review`, `done`, `stop`. So a UI needs one hook (an asyncio queue or a tail of the JSONL file) plus a mapping from these names to UI events; the harness itself does not need changing.

## Q1. Which open-source chat/agent frontends can show tool calls and streaming steps, and how does a Python agent plug in?

### Takeaway
The good-looking, reusable pieces are React component kits (assistant-ui, Vercel AI Elements, CopilotKit) fed by a small Python streaming server (AG-UI or the AI SDK UI-message-stream, both plain SSE). Full apps (Open WebUI, LibreChat, OpenHands, Hermes Desktop, Codex app) either lock you to their own agent, carry licence strings, or are too heavy to fork in two weeks. Hermes Desktop itself is built on assistant-ui, which is the strongest evidence that assistant-ui gives that look.

### Cited Findings

**OpenAI Codex (CLI + app)**
- `openai/codex` is Apache-2.0, mostly Rust, 135.8k stars, pushed 2026-11-18; repo top level holds `codex-cli`, `codex-rs`, `sdk`, `docs` and no desktop-app source — [GitHub openai/codex](https://github.com/openai/codex) (Automated check via GitHub API).
- The Codex App Server is a JSON-RPC protocol plus long-lived process hosting Codex threads, built partly so the desktop app could run many agents; transport is stdio by default, WebSocket optional; OpenAI marks the app-server command and WebSocket transport experimental — [Codex App Server docs](https://developers.openai.com/codex/app-server); [OpenAI: Unlocking the Codex harness](https://openai.com/index/unlocking-the-codex-harness/).
- I found no source showing the Codex desktop app UI itself is open source; only the app-server/CLI are in the public repo. Treat the desktop app as closed (Unverified that it is closed; verified it is not in the repo).

**Hermes Agent desktop app (Nous Research)**
- `NousResearch/hermes-agent` is MIT, Python, 264k stars, pushed 2026-11-18 — [GitHub](https://github.com/NousResearch/hermes-agent).
- Desktop app lives at `apps/desktop`: Electron 44 + React 19 + Vite 8 + Tailwind 4, and its chat uses `@assistant-ui/react` 0.14.29, `@assistant-ui/core`, `@assistant-ui/react-streamdown` — [apps/desktop/package.json](https://github.com/NousResearch/hermes-agent/blob/main/apps/desktop/package.json) (Source inspected).
- README: "native desktop shell for Hermes Agent" with streaming responses, live tool activity, structured tool summaries, side-by-side previews, file browser, voice; it connects to a Hermes gateway or local Hermes install — [apps/desktop/README.md](https://github.com/NousResearch/hermes-agent/tree/main/apps/desktop).
- Hermes also ships an `acp_adapter/` package (ACP server for editors) — [repo tree](https://github.com/NousResearch/hermes-agent/tree/main/acp_adapter).

**OpenHands GUI**
- `OpenHands/OpenHands` is MIT, TypeScript, 93.7k stars, pushed 2026-11-18 — [GitHub](https://github.com/OpenHands/OpenHands). Its GUI is built around the OpenHands agent/runtime; I found no documented way to attach an external Python agent (Gap).

**Open WebUI**
- Licence is the custom "Open WebUI License" (GitHub reports NOASSERTION): BSD-3 style plus clause 4 forbidding removal or alteration of "Open WebUI" branding unless the deployment has 50 or fewer end users in a rolling 30 days, or written permission / enterprise licence — [LICENSE](https://github.com/open-webui/open-webui/blob/main/LICENSE) (Source inspected). Svelte, 159k stars.
- A custom Python agent plugs in as a "Pipe" function; it receives `__event_emitter__` and can emit `status` events (`description`, `done`, `hidden`) shown as progress lines in chat; you must send a final `done: True` or the shimmer persists — [Open WebUI events docs](https://docs.openwebui.com/features/plugin/events).

**LibreChat**
- `LibreChat-AI/LibreChat` is MIT, TypeScript, 47.3k stars, pushed 2026-11-18; describes itself as an "Enhanced ChatGPT Clone" with agents, MCP, artifacts — [GitHub](https://github.com/danny-avila/LibreChat). Integration path for a custom agent would be an OpenAI-compatible custom endpoint (not verified in docs this session; Gap).

**assistant-ui**
- MIT, TypeScript/React, 13.3k stars, pushed 2026-11-18 — [GitHub](https://github.com/assistant-ui/assistant-ui).
- Backends listed: Vercel AI SDK, LangGraph, LangChain, Google ADK, AG-UI, A2A, OpenCode, "Your own server"; has Tool UI and Generative UI docs; setup via `npx assistant-ui@latest setup` — [assistant-ui docs](https://www.assistant-ui.com/docs).

**Vercel AI SDK + AI Elements**
- `vercel/ai` and `vercel/ai-elements` both Apache-2.0 (GitHub shows NOASSERTION; LICENSE text is Apache 2.0) — [vercel/ai LICENSE](https://github.com/vercel/ai/blob/main/LICENSE), [ai-elements LICENSE](https://github.com/vercel/ai-elements/blob/main/LICENSE) (Source inspected).
- AI Elements (shadcn/ui based) ships components including `plan`, `task`, `tool`, `chain-of-thought`, `reasoning`, `queue`, `terminal`, `model-selector`, `context`, `conversation`, `prompt-input`, `code-block`, `test-results` — [packages/elements/src](https://github.com/vercel/ai-elements/tree/main/packages/elements/src) (Source inspected). `context.tsx` shows used/max tokens and usage via `tokenlens` (Source inspected).
- UI message stream protocol: SSE `data:` JSON lines, header `x-vercel-ai-ui-message-stream: v1`, parts `start`, `text-start/delta/end`, `reasoning-*`, `tool-input-start/delta/available`, `tool-output-available`, `start-step`/`finish-step`, custom `data-*`, `error`, `finish`, ends with `[DONE]`; docs say any language (e.g. Python FastAPI) can implement it and link a FastAPI example — [AI SDK stream protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol). The repo has an `examples/next-fastapi` example — [vercel/ai examples](https://github.com/vercel/ai/tree/main/examples) (Source inspected).

**CopilotKit / AG-UI**
- CopilotKit MIT, 38.8k stars ("Makers of the AG-UI Protocol"); `ag-ui-protocol/ag-ui` MIT, 20.3k stars, both pushed 2026-11-18 — [CopilotKit](https://github.com/CopilotKit/CopilotKit), [ag-ui](https://github.com/ag-ui-protocol/ag-ui).
- AG-UI events: lifecycle `RunStarted/RunFinished/RunError`; `StepStarted/StepFinished`; `TextMessageStart/Content/End`; `ToolCallStart/Args/End/Result`; `StateSnapshot/StateDelta/MessagesSnapshot`; `Custom`, `Raw` — [AG-UI events](https://docs.ag-ui.com/concepts/events).
- Python SDK `pip install ag-ui-protocol` (Pydantic models, `ag_ui.encoder.EventEncoder` produces SSE `data:` lines) — [sdks/python README](https://github.com/ag-ui-protocol/ag-ui/tree/main/sdks/python), [AG-UI Python docs](https://docs.ag-ui.com/sdk/python/core/overview).

**Agent Client Protocol (ACP) / Zed**
- Spec repo `agentclientprotocol/agent-client-protocol` Apache-2.0; Python SDK `agentclientprotocol/python-sdk` Apache-2.0, pushed 2026-11-16 — [spec](https://github.com/agentclientprotocol/agent-client-protocol), [python-sdk](https://github.com/agentclientprotocol/python-sdk).
- Clients listed include Zed, JetBrains, VS Code extensions, Neovim, Emacs, Obsidian, Chrome extensions and more — [ACP clients](https://agentclientprotocol.com/overview/clients).
- Zed: add under `agent_servers` with `"type": "custom"`, `command`, `args`, `env` — [Zed external agents](https://zed.dev/docs/ai/external-agents).

**Chainlit**
- Apache-2.0, Python, 12.5k stars, last push 2026-09-18, latest release 2.12.0 (2026-08-25) — [GitHub](https://github.com/Chainlit/chainlit) (Automated check).
- Community-maintained since the original team stepped back on 2025-05-01; maintainers group under a Maintainer Agreement, no company guarantees — [Chainlit README](https://github.com/Chainlit/chainlit), [PyPI](https://pypi.org/project/chainlit/).

**Gradio / Streamlit**
- Gradio Apache-2.0 (44.1k stars), Streamlit Apache-2.0 (46.4k stars), both pushed 2026-11-18 — [gradio](https://github.com/gradio-app/gradio), [streamlit](https://github.com/streamlit/streamlit).
- `gr.Chatbot` renders `ChatMessage(metadata={title, log, duration, status: "pending"|"done", id, parent_id})` as collapsible thought/tool accordions with spinner and nesting — [Gradio agents guide](https://www.gradio.app/guides/agents-and-tool-usage).

### Inferences
- Open WebUI's branding clause is harmless for a class demo (<50 users) but it looks like Open WebUI, not like our product; status events are text lines, so a step tree and cost panel would be awkward.
- OpenHands, LibreChat and Hermes Desktop are whole products tied to their own backends; forking one to speak our trace is more work than building a thin app from the same component kit they use.
- ACP/Zed gives a native-quality agent panel almost for free, but it looks like an editor, not ChatGPT, and live cost/model display has no first-class slot.

### Gaps
- Codex desktop app source: no source found either way beyond its absence from `openai/codex`.
- LibreChat and OpenHands docs for attaching an external agent were not read this session.
- No Python FastAPI example for assistant-ui was read; assistant-ui consumes AG-UI and AI SDK streams, so either Python emitter should work (Unverified).

## Q2. Per option: licence, stack, plug-in protocol, effort, closeness to Codex/ChatGPT look

### Takeaway
Ranked by look-per-effort for our harness (efforts are estimates for 2-3 students who know some React):

| Option | Licence | Stack | How our Python agent plugs in | Effort (est.) | Look vs Codex/ChatGPT |
|---|---|---|---|---|---|
| assistant-ui + FastAPI SSE (AG-UI or AI SDK stream) | MIT | React/Tailwind | FastAPI endpoint maps `Trace.log` rows to AG-UI or UI-message-stream events | 4-7 days | Very close (Hermes Desktop uses it) |
| Next.js + AI SDK `useChat` + AI Elements | Apache-2.0 | React/shadcn | Same FastAPI SSE using UI-message-stream; `data-*` parts for route/cost | 4-7 days | Very close; has Plan, Task, Tool, Context components |
| CopilotKit + AG-UI | MIT | React | `ag-ui-protocol` EventEncoder over SSE | 5-8 days | Close; more sidebar/copilot styled |
| Gradio `gr.Chatbot` with metadata | Apache-2.0 | Python only | Call harness in-process, yield `ChatMessage`s | 1-2 days | Fair; clean but recognisably Gradio |
| Chainlit Steps | Apache-2.0 | Python + React bundle | `cl.Step` per plan step in-process | 1-3 days | Good ChatGPT-like look; community-maintained since 2025-05 |
| Open WebUI Pipe | Open WebUI License (branding clause) | Svelte/Python | Pipe + `__event_emitter__` status | 2-3 days | ChatGPT-like, but Open WebUI branded, weak step/cost display |
| Zed via ACP | Apache-2.0 (protocol) | Rust editor | `agentclientprotocol` Python SDK over stdio | 2-4 days | Editor agent panel, not a chat app |
| Fork Hermes Desktop / OpenHands / LibreChat | MIT | Electron/React, TS | Replace their backend client | 2+ weeks | Very close but high risk |

### Cited Findings
- See Q1 citations for licences, stacks and protocols: [assistant-ui docs](https://www.assistant-ui.com/docs), [AI SDK stream protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol), [AI Elements src](https://github.com/vercel/ai-elements/tree/main/packages/elements/src), [AG-UI events](https://docs.ag-ui.com/concepts/events), [Gradio agents guide](https://www.gradio.app/guides/agents-and-tool-usage), [Chainlit README](https://github.com/Chainlit/chainlit), [Open WebUI LICENSE](https://github.com/open-webui/open-webui/blob/main/LICENSE), [Zed external agents](https://zed.dev/docs/ai/external-agents), [Hermes desktop package.json](https://github.com/NousResearch/hermes-agent/blob/main/apps/desktop/package.json).

### Inferences
- Mapping our trace to AG-UI (one-to-one enough): `plan` -> `StateSnapshot` (DAG) + `StepStarted` per node; `route` -> `Custom{name:"route", model, tier, laya_score}`; `llm_call` -> `ToolCallStart/Args/End/Result` labelled with the model, plus `StateDelta` on running cost; `escalate`/`takeover` -> `Custom` badges; `step_end` -> `StepFinished`; `done` -> final `TextMessage*` + `RunFinished`.
- Same mapping to AI SDK stream: `start-step`/`finish-step` per DAG node, `tool-input-available`/`tool-output-available` per model call, `data-route` and `data-cost` custom parts, `text-delta` for the final answer.
- Harbor runs take minutes and use Docker; the UI server should launch the run as a background task and stream from the trace queue, and also support replaying a saved JSONL so the demo cannot fail live (the repo already has `phase2/replay.py` and `show_trace.py`).

### Gaps
- Effort figures are not sourced; they assume the team already has a FastAPI endpoint that can start a run.

## Q3. Best look for least work in 1-2 weeks

### Takeaway
Recommend: FastAPI SSE server emitting AG-UI events from a `Trace.log` hook (or replaying a JSONL), with a Vite/Next React page built from assistant-ui (thread + tool UIs) and borrowed AI Elements pieces (Plan, Task, Context for tokens/cost). This is the same component library Hermes Desktop uses, gives a Codex/ChatGPT look, keeps our harness untouched, and fits about one week, leaving week two for polish and a replay mode. Fallback if React is out of reach: Gradio `gr.Chatbot` with metadata accordions in 1-2 days.

### Cited Findings
- Hermes Desktop's chat is built on `@assistant-ui/react` — [package.json](https://github.com/NousResearch/hermes-agent/blob/main/apps/desktop/package.json).
- assistant-ui supports AG-UI and custom servers — [assistant-ui docs](https://www.assistant-ui.com/docs).
- AG-UI Python SDK encodes events to SSE — [ag-ui sdks/python](https://github.com/ag-ui-protocol/ag-ui/tree/main/sdks/python).
- AI Elements provides `plan`, `task`, `tool`, `context`, `model-selector` components under Apache-2.0 — [ai-elements](https://github.com/vercel/ai-elements/tree/main/packages/elements/src).
- Gradio thought/tool accordions with status and duration — [Gradio guide](https://www.gradio.app/guides/agents-and-tool-usage).

### Inferences
- Plan: day 1-2 FastAPI `/run` (start harness, push `Trace.log` rows to a queue) + `/replay?trace=` and AG-UI encoding; day 3-5 assistant-ui thread, step list, model badge, live cost counter in a side panel; day 6-7 replay of a recorded passing and an escalating trial for the demo.
- Choose AG-UI over the AI SDK stream if the team wants a protocol name for the report; choose the AI SDK stream if they start from Next.js and `useChat`. Both are SSE and roughly equal work.
- Avoid Chainlit as the main path: good look, but community-maintained with last release 2026-08-25.

### Gaps
- Not tested locally: no prototype was built, so the effort figures are estimates only.
