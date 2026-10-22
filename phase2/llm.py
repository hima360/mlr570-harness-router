"""Model calls through opencodex (OpenAI-compatible /v1/chat/completions)."""
import os

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")


def litellm_name(model: str) -> str:
    """opencodex ids go to litellm's OpenAI-compatible client unchanged after the `openai/` prefix."""
    return f"openai/{model}"


def chat(cfg, ledger, model: str, messages: list[dict], **kwargs) -> str:
    """One blocking chat call; records usage on the ledger and returns the reply text."""
    from .cli_models import complete_fn_for

    cli_complete = complete_fn_for(model, cfg)
    if cli_complete is not None:
        text, usage = cli_complete(model, messages)
        ledger.add_from_usage(model, usage)
        return text

    import litellm

    resp = litellm.completion(model=litellm_name(model), messages=messages,
                              api_base=cfg.base_url, api_key=cfg.api_key, **kwargs)
    if getattr(resp, "usage", None) is not None:
        ledger.add_from_usage(model, resp.usage)
    return resp.choices[0].message.content or ""
