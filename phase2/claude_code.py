"""Claude Code as a model backend: `claude -p` (Hima's Claude subscription) as a text-in / text-out model.

Model ids look like `claude/<model>` (e.g. `claude/claude-sonnet-5-5`). Each call is stateless and runs
with no tools, no session file and safe mode (no CLAUDE.md, hooks, skills or MCP), and with our own
system prompt instead of Claude Code's, which keeps the per-call overhead small.
"""
import os
import tempfile

from .cli_models import PROMPT_HEADER, openai_usage, run_cli, run_group, serialize

PREFIX = "claude/"

# Stripped from the child env so `claude -p` always uses the subscription login, never a
# pay-per-token API key or a third-party endpoint.
API_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


def is_claude_code(model: str) -> bool:
    return model.startswith(PREFIX)


def complete(model: str, messages: list[dict], effort: str | None = None, timeout: int = 600,
             run=run_group, max_output_tokens: int = 0) -> tuple[str, dict]:
    """Returns (reply text, OpenAI-style usage incl. claude_code_cost_usd). `run` is injectable for tests."""
    model_id = model[len(PREFIX):] if is_claude_code(model) else model
    system = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    rest = [m for m in messages if m.get("role") != "system"]
    env = {k: v for k, v in os.environ.items() if k not in API_ENV_VARS}
    if max_output_tokens:  # cheap tiers must not write 16k-token replies (they hit the call timeout)
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
    with tempfile.TemporaryDirectory() as d:
        argv = ["claude", "-p", "--model", model_id, "--output-format", "json", "--tools", "",
                "--safe-mode", "--no-session-persistence", "--strict-mcp-config",
                "--system-prompt", (PROMPT_HEADER + system).strip()] + (["--effort", effort] if effort else [])
        result = run_cli(lambda: run(argv, input=serialize(rest, header=""), cwd=d, env=env,
                                     capture_output=True, text=True, timeout=timeout), "claude -p",
                         model=model)
    return result.get("result") or "", openai_usage(result.get("usage") or {},
                                                    claude_code_cost_usd=result.get("total_cost_usd"))
