"""opencodex as a text-in / text-out model backend, a drop-in for the Droid CLI backend (Droid ended 2026-11-03).

Model ids look like `ocx/<opencodex id>`, e.g. `ocx/zai/glm-5.3-flash` or `ocx/anthropic/claude-opus-5-5`.
Calls go to opencodex's OpenAI-compatible chat route as real chat messages, so the provider caches the shared
prefix (measured 2026-11-18: a repeated 16k-token prompt read 16,235 tokens from cache on Opus 5.5, 8,960 of
9,021 on GLM-5.3 Flash). A `CliSession` keeps the conversation here: a resumed call gets only the new turns and
this module re-sends the whole history, which the provider serves from cache, so callers see the same contract
as `droid exec -s`.
"""
import os
import time
import uuid

PREFIX = "ocx/"
DEFAULT_BASE = "http://127.0.0.1:10100/v1"
_HISTORIES: dict[str, list[dict]] = {}  # session id -> full message history (for resume and fork)
# Z.ai answers bursts with "429 Rate limit reached for requests" (2026-11-18: 4 parallel runs, GLM builders died on
# their first calls and Opus did the whole task). Droid retried for us; here we back off and retry transient errors.
RETRY_WAITS = (20, 40, 60, 90, 120, 120, 180)
TRANSIENT = ("429", "rate limit", "ratelimit", "overloaded", "529", "502", "503", "504", "timeout", "timed out",
             "connection reset", "connection error", "temporarily unavailable", "incomplete stream",
             "apiconnectionerror", "internal server error", "midstreamfallback", "server_error")


def transient(err: Exception) -> bool:
    text = f"{type(err).__name__} {err}".lower()
    return any(k in text for k in TRANSIENT)


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


class IncompleteReply(Exception):
    """The stream ended without text and without hitting the length limit (2026-11-19: opencodex logged Opus
    streams as `incomplete / upstream_server_error` with HTTP 200; the empty text ended a takeover in
    RepeatedFormatError). Retried like any transient provider error."""


def _collect(resp) -> tuple[str, object]:
    """Text and usage from a streamed reply (2026-11-18: long non-streamed GLM replies hit opencodex's 200 s
    no-output limit and came back as 502; Droid streamed). A plain response object is accepted too (tests)."""
    if getattr(resp, "choices", None) and getattr(resp.choices[0], "message", None) is not None:
        return resp.choices[0].message.content or "", getattr(resp, "usage", None)
    parts, usage, finish = [], None, None
    for ch in resp:
        if getattr(ch, "choices", None):
            delta = getattr(ch.choices[0], "delta", None)
            if delta is not None and getattr(delta, "content", None):
                parts.append(delta.content)
            finish = getattr(ch.choices[0], "finish_reason", None) or finish
        if getattr(ch, "usage", None):
            usage = ch.usage
    text = "".join(parts)
    if not text.strip() and finish == "content_filter":  # a refusal: retrying gets the same answer
        raise RuntimeError("provider refused the request (finish_reason=content_filter)")
    if not text.strip() and finish != "length":
        raise IncompleteReply(f"incomplete stream: no text (finish_reason={finish})")
    return text, usage


def _responses_body(model_id: str, history: list[dict], effort, max_output_tokens: int) -> dict:
    """A Responses-route request. Assistant turns carry the reasoning item they came with, which opencodex
    turns back into `reasoning_content` for models on its preserve list (GLM-5.x). System text goes in
    `instructions`: the chat adapter drops developer messages for non-OpenAI upstreams."""
    system = "\n\n".join(m["content"] for m in history if m["role"] == "system")
    items = []
    for m in history:
        if m["role"] == "system":
            continue
        if m["role"] == "assistant" and m.get("reasoning"):
            items.append(m["reasoning"])
        items.append({"role": m["role"], "content": m["content"]})
    body = {"model": model_id, "input": items, "stream": True, "store": False,
            "reasoning": {"summary": "auto", **({"effort": effort} if effort else {})}}
    if system:
        body["instructions"] = system
    if max_output_tokens:
        body["max_output_tokens"] = max_output_tokens
    return body


def _responses_collect(lines) -> tuple[str, dict | None, dict]:
    """(text, reasoning item, usage) from a Responses SSE stream; the final `response.completed` event holds
    the whole output. A stream that ends without it is an incomplete reply."""
    import json

    final = None
    for line in lines:
        if line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                ev = json.loads(data)
                if ev.get("type") in ("response.completed", "response.incomplete", "response.failed"):
                    final = ev
    resp = (final or {}).get("response") or {}
    if not final or final.get("type") == "response.failed":
        raise IncompleteReply(f"incomplete stream: {(resp.get('error') or {}).get('message', 'no completed event')}")
    text, reasoning = "", None
    for it in resp.get("output") or []:
        if it.get("type") == "reasoning":
            reasoning = {k: v for k, v in it.items() if k in ("type", "id", "summary", "content", "encrypted_content")}
        elif it.get("type") == "message":
            text += "".join(c.get("text", "") for c in it.get("content") or [] if c.get("type") == "output_text")
    u = resp.get("usage") or {}
    usage = {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0),
             "prompt_tokens_details": {"cached_tokens": (u.get("input_tokens_details") or {}).get("cached_tokens", 0)}}
    cut = final.get("type") == "response.incomplete"
    if not text.strip() and not cut:
        raise IncompleteReply("incomplete stream: no text")
    return text, reasoning, usage


def _responses_call(model_id, history, effort, timeout, max_output_tokens):
    import httpx

    base = os.environ.get("PHASE2_OCX_BASE", DEFAULT_BASE)
    body = _responses_body(model_id, history, effort, max_output_tokens)
    headers = {"Authorization": f"Bearer {os.environ.get('PHASE2_OCX_KEY', 'local')}"}
    with httpx.stream("POST", f"{base}/responses", json=body, headers=headers, timeout=timeout) as r:
        if r.status_code >= 400:
            r.read()
            raise RuntimeError(f"{r.status_code} {r.text[:300]}")
        return _responses_collect(r.iter_lines())


def complete(model: str, messages: list[dict], effort: str | None = None, timeout: int = 600, session=None,
             max_output_tokens: int = 0, call=None, sleep=time.sleep, replay_reasoning: bool = False,
             responses_call=None) -> tuple[str, dict]:
    """Returns (reply text, OpenAI-style usage). `call` is injectable for tests (same signature as
    litellm.completion). With a `CliSession`, `messages` are only the turns the session has not seen.
    `replay_reasoning` (v4.1) uses the Responses route and keeps each reply's reasoning in the history."""
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
    reasoning = None
    for i, wait in enumerate((*RETRY_WAITS, None)):
        try:
            if replay_reasoning:
                text, reasoning, usage = (responses_call or _responses_call)(model_id, history, effort, timeout,
                                                                             max_output_tokens)
            else:
                resp = call(model=f"openai/{model_id}", messages=[{"role": m["role"], "content": m["content"]}
                                                                  for m in history], timeout=timeout,
                            api_base=os.environ.get("PHASE2_OCX_BASE", DEFAULT_BASE),
                            api_key=os.environ.get("PHASE2_OCX_KEY", "local"), stream=True,
                            stream_options={"include_usage": True}, **kwargs)
                text, usage = _collect(resp)
            break
        except Exception as e:
            if wait is None or not transient(e):
                raise
            sleep(wait)
    if session is not None:
        if not session.id:
            session.id = uuid.uuid4().hex
        turn = {"role": "assistant", "content": text}
        if reasoning:
            turn["reasoning"] = reasoning
        _HISTORIES[session.id] = history + [turn]
    return text, _usage(usage or {})
