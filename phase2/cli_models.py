"""Shared plumbing for CLI-backed models (`droid exec`, `claude -p`) used as text-in / text-out models.

Each call is stateless: the conversation is serialized into one prompt, and the CLI prints one JSON
`result` object with the reply and usage. Both CLIs add their own system prompt and per-call
overhead, so token counts here are inflated versus a raw API.
"""
import json

PROMPT_HEADER = ("You are acting as a plain language model inside another program. Do not use any "
                 "tools, do not read or write files. Reply only with the next ASSISTANT message for "
                 "the conversation below, following its instructions exactly.\n\n")


def serialize(messages: list[dict], header: str = PROMPT_HEADER) -> str:
    parts = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):  # multimodal / structured content: keep the text parts
            content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        parts.append(f"### {m.get('role', 'user').upper()}\n{content or ''}")
    return header + "\n\n".join(parts) + "\n\n### ASSISTANT\n"


def parse_result(proc, cli: str) -> dict:
    """Last JSON line with type == result; raises on a missing or error result."""
    result = None
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            result = obj
            break
    if result is None or result.get("is_error"):
        raise RuntimeError(f"{cli} failed (rc={proc.returncode}): {(proc.stderr or proc.stdout or '')[-500:]}")
    return result


def openai_usage(u: dict, **extra) -> dict:
    """Anthropic-style usage -> OpenAI-style. prompt_tokens includes cache reads and cache writes."""
    cached = int(u.get("cache_read_input_tokens") or 0)
    prompt = int(u.get("input_tokens") or 0) + cached + int(u.get("cache_creation_input_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": int(u.get("output_tokens") or 0),
            "prompt_tokens_details": {"cached_tokens": cached}, **extra}


def complete_fn_for(model: str):
    """The CLI backend's complete(model, messages) for this model id, or None for opencodex models."""
    from . import claude_code, droid

    if droid.is_droid(model):
        return droid.complete
    if claude_code.is_claude_code(model):
        return claude_code.complete
    return None


def make_text_model(model: str, on_usage, complete_fn=None):
    """mini-swe-agent text-based model whose completions come from a CLI backend."""
    import litellm
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    complete_fn = complete_fn or complete_fn_for(model)

    class CliTextModel(LitellmTextbasedModel):
        def _query(self, messages, **kwargs):
            text, usage = complete_fn(model, messages)
            on_usage(usage)
            return litellm.ModelResponse(
                model=model,
                choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                usage={k: usage[k] for k in ("prompt_tokens", "completion_tokens")},
            )

        def _calculate_cost(self, response):
            # The Ledger prices CLI calls; litellm cannot resolve droid/ or claude/ ids and
            # prints a "Provider List" error on every lookup.
            return {"cost": 0.0}

    return CliTextModel(model_name=model, cost_tracking="ignore_errors")
