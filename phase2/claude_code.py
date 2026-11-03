"""Claude Code as a model backend: `claude -p` (Hima's Claude subscription) as a text-in / text-out model.

Model ids look like `claude/<model>` (e.g. `claude/claude-sonnet-5-5`). Each call is stateless and runs
with no tools, no session file and safe mode (no CLAUDE.md, hooks, skills or MCP), and with our own
system prompt instead of Claude Code's, which keeps the per-call overhead small.
"""
import os
import tempfile

import contextlib

from .cli_models import PROMPT_HEADER, RETRY_NOTE, delta_text, openai_usage, run_cli, run_group, serialize

PREFIX = "claude/"

# Stripped from the child env so `claude -p` always uses the subscription login, never a
# pay-per-token API key or a third-party endpoint.
API_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


def is_claude_code(model: str) -> bool:
    return model.startswith(PREFIX)


def complete(model: str, messages: list[dict], effort: str | None = None, timeout: int = 600,
             run=run_group, max_output_tokens: int = 0, session=None) -> tuple[str, dict]:
    """Returns (reply text, OpenAI-style usage incl. claude_code_cost_usd). `run` is injectable for tests.
    With a `CliSession`, a call resumes that session (`--resume`) and `messages` are only the new turns."""
    model_id = model[len(PREFIX):] if is_claude_code(model) else model
    resume = bool(session is not None and session.id)
    fork = bool(session is not None and not session.id and session.fork_from)
    if fork:  # total_cost_usd of a fork includes the parent's cost (measured 2026-10-27)
        session.seed = {"cost": session.fork_from.get("cost_total", 0.0)}
    system = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    rest = [m for m in messages if m.get("role") != "system"]
    if session is not None:  # the system prompt is the start of the cached prefix: keep it byte-identical
        system = session.system if resume else system  # a fork keeps the parent's own system prompt
        session.system = system
    env = {k: v for k, v in os.environ.items() if k not in API_ENV_VARS}
    if max_output_tokens:  # cheap tiers must not write 16k-token replies (they hit the call timeout)
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
    where = contextlib.nullcontext(session.dir) if session is not None else tempfile.TemporaryDirectory()
    with where as d:
        argv = ["claude", "-p", "--model", model_id, "--output-format", "json", "--tools", "",
                "--safe-mode", "--strict-mcp-config",
                "--system-prompt", (PROMPT_HEADER + system).strip()] + (["--effort", effort] if effort else []) + \
               (["--resume", session.id] if resume else []) + \
               (["--resume", session.fork_from["id"], "--fork-session"] if fork else []) + \
               (["--no-session-persistence"] if session is None else [])

        def call(extra=()):
            msgs = [*rest, *extra]
            return run(argv, input=delta_text(msgs) if (resume or fork) else serialize(msgs, header=""), cwd=d, env=env,
                       capture_output=True, text=True, timeout=timeout)

        result = run_cli(call, "claude -p", model=model,
                         retry_call=lambda: call([{"role": "user", "content": RETRY_NOTE}]))
    cost = result.get("total_cost_usd")
    if session is not None:  # usage is per call, but total_cost_usd is a running total over the session
        cost = session.per_call({"cost": cost or 0.0}, result.get("session_id"), ("cost",))["cost"]
    usage = openai_usage(result.get("usage") or {}, claude_code_cost_usd=cost)
    if result.get("stop_reason") == "max_tokens":
        usage["truncated"] = True
    return result.get("result") or "", usage
