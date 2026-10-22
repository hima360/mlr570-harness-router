"""Factory Droid as a model backend: `droid exec` used as a plain text-in / text-out model.

Model ids look like `droid/<droid model id>` (e.g. `droid/kimi-k3`; `droid exec -m nope x`
lists the valid ids). Each call is stateless: the whole conversation is serialized into one
prompt. Droid adds its own system prompt (~7k input tokens per call, measured 2026-10-22), so
token counts and costs here are inflated versus a raw API and are for the demo only.
"""
import tempfile
from pathlib import Path

from .cli_models import openai_usage, run_cli, run_group, serialize

PREFIX = "droid/"


def is_droid(model: str) -> bool:
    return model.startswith(PREFIX)


def complete(model: str, messages: list[dict], effort: str | None = None, timeout: int = 600,
             run=run_group) -> tuple[str, dict]:
    """Returns (reply text, OpenAI-style usage dict incl. factory_credits). `run` is injectable for tests."""
    model_id = model[len(PREFIX):] if is_droid(model) else model
    with tempfile.TemporaryDirectory() as d:
        prompt = Path(d) / "prompt.txt"
        prompt.write_text(serialize(messages))
        # Only TodoWrite (no side effects) stays allowed: otherwise Droid's read-only Execute, Read,
        # Grep, WebSearch and FetchUrl run on the host, outside the harness and the task container.
        argv = ["droid", "exec", "-m", model_id, "-o", "json", "--disable-builtin-skills",
                "--only-tools", "TodoWrite", "-f", str(prompt)] + (["-r", effort] if effort else [])
        result = run_cli(lambda: run(argv, cwd=d, capture_output=True, text=True, timeout=timeout),
                         "droid exec")
    u = result.get("usage") or {}
    return result.get("result") or "", openai_usage(u, factory_credits=u.get("factory_credits"))
