"""Factory Droid as a model backend: `droid exec` used as a plain text-in / text-out model.

Model ids look like `droid/<droid model id>` (e.g. `droid/kimi-k3`; `droid exec -m nope x`
lists the valid ids). Each call is stateless: the whole conversation is serialized into one
prompt. Droid adds its own system prompt (~7k input tokens per call, measured 2026-10-22), so
token counts and costs here are inflated versus a raw API and are for the demo only.
"""
import contextlib
import os
import tempfile
from pathlib import Path

from .cli_models import RETRY_NOTE, delta_text, openai_usage, run_cli, run_group, serialize

# droid exec -s reports these as running totals over the session (measured 2026-10-25)
CUMULATIVE = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
              "factory_credits")

PREFIX = "droid/"
APP_DROID = "/Applications/Factory.app/Contents/Resources/bin/droid"


def droid_bin() -> str:
    """PHASE2_DROID_BIN, else the Factory app's bundled droid (updated with the app), else droid on PATH.
    An older droid on PATH (0.226.1 here) falls back to a stale built-in model list when it cannot fetch
    the live catalog, and then rejects current models like gpt-6-sol as invalid."""
    return os.environ.get("PHASE2_DROID_BIN") or (APP_DROID if Path(APP_DROID).exists() else "droid")


def is_droid(model: str) -> bool:
    return model.startswith(PREFIX)


def complete(model: str, messages: list[dict], effort: str | None = None, timeout: int = 600,
             run=run_group, session=None) -> tuple[str, dict]:
    """Returns (reply text, OpenAI-style usage dict incl. factory_credits). `run` is injectable for tests.
    With a `CliSession`, a call continues that session (`-s`) and `messages` are only the new turns."""
    model_id = model[len(PREFIX):] if is_droid(model) else model
    resume = bool(session is not None and session.id)

    def text(extra=()):
        msgs = [*messages, *extra]
        return delta_text(msgs) if resume else serialize(msgs)

    where = contextlib.nullcontext(session.dir) if session is not None else tempfile.TemporaryDirectory()
    with where as d:
        prompt = Path(d) / "prompt.txt"
        prompt.write_text(text())
        # Only TodoWrite (no side effects) stays allowed: otherwise Droid's read-only Execute, Read,
        # Grep, WebSearch and FetchUrl run on the host, outside the harness and the task container.
        argv = [droid_bin(), "exec", "-m", model_id, "-o", "json", "--disable-builtin-skills",
                "--only-tools", "TodoWrite", "-f", str(prompt)] + (["-r", effort] if effort else []) + \
               (["-s", session.id] if resume else [])

        def retry():
            prompt.write_text(text([{"role": "user", "content": RETRY_NOTE}]))
            return run(argv, cwd=d, capture_output=True, text=True, timeout=timeout)

        result = run_cli(lambda: run(argv, cwd=d, capture_output=True, text=True, timeout=timeout),
                         "droid exec", model=model, retry_call=retry)
    u = result.get("usage") or {}
    if session is not None:
        u = session.per_call(u, result.get("session_id"), CUMULATIVE)
    return result.get("result") or "", openai_usage(u, factory_credits=u.get("factory_credits"))
