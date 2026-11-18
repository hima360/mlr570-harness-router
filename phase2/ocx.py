"""opencodex as a text-in / text-out model backend, a drop-in for the Droid CLI backend (Droid ended 2026-11-03).

Model ids look like `ocx/<opencodex id>`, e.g. `ocx/zai/glm-5.3-flash` or `ocx/anthropic/claude-opus-5-5`.
Calls go to opencodex's OpenAI-compatible chat route as real chat messages, so the provider caches the shared
prefix (measured 2026-11-18: a repeated 16k-token prompt read 16,235 tokens from cache on Opus 5.5, 8,960 of
9,021 on GLM-5.3 Flash). A `CliSession` keeps the conversation here: a resumed call gets only the new turns and
this module re-sends the whole history, which the provider serves from cache, so callers see the same contract
as `droid exec -s`.
"""
import os
import uuid

PREFIX = "ocx/"
DEFAULT_BASE = "http://127.0.0.1:10100/v1"
_HISTORIES: dict[str, list[dict]] = {}  # session id -> full message history (for resume and fork)


def is_ocx(model: str) -> bool:
    return model.startswith(PREFIX)


def _plain(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):  # content blocks -> text
            content = "\n".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content)
        out.append({"role": m.get("role", "user"), "content": str(content or "")})
    return out


def _usage(u) -> dict:
    get = (lambda k: u.get(k)) if isinstance(u, dict) else (lambda k: getattr(u, k, None))
    details = get("prompt_tokens_details") or {}
    cached = (details.get("cached_tokens") if isinstance(details, dict) else getattr(details, "cached_tokens", 0)) or 0
    return {"prompt_tokens": int(get("prompt_tokens") or 0), "completion_tokens": int(get("completion_tokens") or 0),
            "prompt_tokens_details": {"cached_tokens": int(cached)}}


def complete(model: str, messages: list[dict], effort: str | None = None, timeout: int = 600, session=None,
             max_output_tokens: int = 0, call=None) -> tuple[str, dict]:
    """Returns (reply text, OpenAI-style usage). `call` is injectable for tests (same signature as
    litellm.completion). With a `CliSession`, `messages` are only the turns the session has not seen."""
    model_id = model[len(PREFIX):] if is_ocx(model) else model
    new = _plain(messages)
    if session is not None and session.id and session.id in _HISTORIES:
        history = _HISTORIES[session.id] + new
    elif session is not None and not session.id and session.fork_from and session.fork_from.get("id") in _HISTORIES:
        history = list(_HISTORIES[session.fork_from["id"]]) + new
    else:
        history = new
    if call is None:
        import litellm

        call = litellm.completion
    kwargs = {"reasoning_effort": effort} if effort else {}
    if max_output_tokens:
        kwargs["max_tokens"] = max_output_tokens
    resp = call(model=f"openai/{model_id}", messages=history, timeout=timeout,
                api_base=os.environ.get("PHASE2_OCX_BASE", DEFAULT_BASE),
                api_key=os.environ.get("PHASE2_OCX_KEY", "local"), **kwargs)
    text = resp.choices[0].message.content or ""
    if session is not None:
        if not session.id:
            session.id = uuid.uuid4().hex
        _HISTORIES[session.id] = history + [{"role": "assistant", "content": text}]
    return text, _usage(getattr(resp, "usage", None) or {})
