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


def chat(cfg, ledger, model: str, messages: list[dict], on_call=None, **kwargs) -> str:
    """One blocking chat call; records usage on the ledger and returns the reply text.
    `on_call(info)` gets the call record (model, ms, tokens, cost, reply snippet)."""
    from .cli_models import ProviderUnavailable, complete_fn_for

    t0 = time.perf_counter()
    cli_complete = complete_fn_for(model, cfg)
    if cli_complete is not None:
        try:
            text, usage = cli_complete(model, messages)
        except ProviderUnavailable:  # the planner has no fallback model: one more try after a pause
            time.sleep(PLANNER_RETRY_WAIT_SEC)
            t0 = time.perf_counter()
            text, usage = cli_complete(model, messages)
    else:
        import litellm

        resp = litellm.completion(model=litellm_name(model), messages=messages,
                                  api_base=cfg.base_url, api_key=cfg.api_key, **kwargs)
        text, usage = resp.choices[0].message.content or "", getattr(resp, "usage", None)
    info = record_call(ledger, model, usage, (time.perf_counter() - t0) * 1e3, text)
    if on_call:
        on_call(info)
    return text
