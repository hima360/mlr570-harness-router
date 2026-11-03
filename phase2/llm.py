"""Model calls through opencodex (OpenAI-compatible /v1/chat/completions) or a CLI backend."""
import os
import time

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")


def litellm_name(model: str) -> str:
    """opencodex ids go to litellm's OpenAI-compatible client unchanged after the `openai/` prefix."""
    return f"openai/{model}"


def record_call(ledger, model: str, usage, ms: float, reply: str) -> dict:
    """Charge one model call to the ledger; returns what the trace shows for it (who, how long, how much)."""
    u = ledger.read_usage(usage) if usage is not None else {}
    cost = ledger.add_from_usage(model, usage) if usage is not None else 0.0
    return {"model": model, "ms": round(ms), **{k: v for k, v in u.items() if v},
            "cost_usd": round(cost, 6), "reply": " ".join(str(reply or "").split())[:300],
            **{k: usage[k] for k in ("guarded", "session", "team_update") if isinstance(usage, dict) and usage.get(k)}}


PLANNER_RETRY_WAIT_SEC = 5.0


def chat(cfg, ledger, model: str, messages: list[dict], on_call=None, session=None, **kwargs) -> str:
    """One blocking chat call; records usage on the ledger and returns the reply text.
    `on_call(info)` gets the call record (model, ms, tokens, cost, reply snippet). With a `CliSession` (CLI models
    only), a conversation's later calls resume that session and send only the turns it has not seen, so the
    provider caches the rest (2026-10-29: uncached review calls were 41% of v2's credits)."""
    from .cli_models import ProviderUnavailable, complete_fn_for

    t0 = time.perf_counter()
    cli_complete = complete_fn_for(model, cfg)
    if cli_complete is not None:
        extra = {}
        if session is not None:
            new = [m for m in messages[session.seen:] if m.get("role") != "assistant"] if session.id else messages
            messages, extra = new, {"session": session}
        try:
            text, usage = cli_complete(model, messages, **extra)
        except ProviderUnavailable:  # the planner has no fallback model: one more try after a pause
            time.sleep(PLANNER_RETRY_WAIT_SEC)
            t0 = time.perf_counter()
            text, usage = cli_complete(model, messages, **extra)
        if session is not None:
            session.seen += len(messages) + 1  # what it sent, plus the reply the caller appends
    else:
        import litellm

        resp = litellm.completion(model=litellm_name(model), messages=messages,
                                  api_base=cfg.base_url, api_key=cfg.api_key, **kwargs)
        text, usage = resp.choices[0].message.content or "", getattr(resp, "usage", None)
    info = record_call(ledger, model, usage, (time.perf_counter() - t0) * 1e3, text)
    if on_call:
        on_call(info)
    return text
