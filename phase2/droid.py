"""Factory Droid as a model backend: `droid exec` used as a plain text-in / text-out model.

Model ids look like `droid/<droid model id>` (e.g. `droid/kimi-k3`; `droid exec -m nope x`
lists the valid ids). Each call is stateless: the whole conversation is serialized into one
prompt. Droid adds its own system prompt (~7k input tokens per call, measured 2026-10-22), so
token counts and costs here are inflated versus a raw API and are for the demo only.
"""
import json
import subprocess
import tempfile
from pathlib import Path

PREFIX = "droid/"

PROMPT_HEADER = ("You are acting as a plain language model inside another program. Do not use any "
                 "tools, do not read or write files. Reply only with the next ASSISTANT message for "
                 "the conversation below, following its instructions exactly.\n\n")


def is_droid(model: str) -> bool:
    return model.startswith(PREFIX)


def serialize(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):  # multimodal / structured content: keep the text parts
            content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        parts.append(f"### {m.get('role', 'user').upper()}\n{content or ''}")
    return PROMPT_HEADER + "\n\n".join(parts) + "\n\n### ASSISTANT\n"


def complete(model: str, messages: list[dict], timeout: int = 600, run=subprocess.run) -> tuple[str, dict]:
    """Returns (reply text, OpenAI-style usage dict incl. factory_credits). `run` is injectable for tests."""
    model_id = model[len(PREFIX):] if is_droid(model) else model
    with tempfile.TemporaryDirectory() as d:
        prompt = Path(d) / "prompt.txt"
        prompt.write_text(serialize(messages))
        p = run(["droid", "exec", "-m", model_id, "-o", "json", "--disable-builtin-skills", "-f", str(prompt)],
                cwd=d, capture_output=True, text=True, timeout=timeout)
    result = None
    for line in reversed((p.stdout or "").strip().splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "result":
            result = obj
            break
    if result is None or result.get("is_error"):
        raise RuntimeError(f"droid exec failed (rc={p.returncode}): {(p.stderr or p.stdout or '')[-500:]}")
    u = result.get("usage") or {}
    cached = int(u.get("cache_read_input_tokens") or 0)
    prompt_tokens = int(u.get("input_tokens") or 0) + cached + int(u.get("cache_creation_input_tokens") or 0)
    usage = {"prompt_tokens": prompt_tokens, "completion_tokens": int(u.get("output_tokens") or 0),
             "prompt_tokens_details": {"cached_tokens": cached},
             "factory_credits": u.get("factory_credits")}
    return result.get("result") or "", usage


def make_text_model(model: str, on_usage, complete_fn=complete):
    """mini-swe-agent text-based model whose completions come from droid exec."""
    import litellm
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    class DroidTextModel(LitellmTextbasedModel):
        def _query(self, messages, **kwargs):
            text, usage = complete_fn(model, messages)
            on_usage(usage)
            return litellm.ModelResponse(
                model=model,
                choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                usage={k: usage[k] for k in ("prompt_tokens", "completion_tokens")},
            )

    return DroidTextModel(model_name=model, cost_tracking="ignore_errors")
