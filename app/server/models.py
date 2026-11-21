"""Display names for model ids and setups. Read-only helpers; nothing here changes the harness."""
import re

PROVIDERS = {"zai": "Z.ai", "anthropic": "Anthropic", "openai": "OpenAI", "deepseek": "DeepSeek",
             "moonshot": "Moonshot"}
BACKENDS = {"ocx": "opencodex", "droid": "Factory Droid", "claude": "Claude Code"}
WORDS = {"glm": "GLM", "gpt": "GPT", "deepseek": "DeepSeek", "kimi": "Kimi", "opus": "Opus", "sonnet": "Sonnet",
         "haiku": "Haiku", "flash": "Flash", "flashx": "FlashX", "sol": "Sol", "luna": "Luna"}

# The four setups a person can start from the app (phase2/setups.py names).
LIVE_SETUPS = [
    {"id": "ocxv4", "label": "v4", "blurb": "GLM-5.3 Flash builds, checks run, Opus 5.5 judges and takes over on fix"},
    {"id": "ocx28", "label": "v2.8", "blurb": "GLM-5.3 Flash builds, Opus 5.5 judges once and takes over on fix"},
    {"id": "ocxopus", "label": "Opus alone", "blurb": "Opus 5.5 does the whole task"},
    {"id": "ocxglm", "label": "GLM alone", "blurb": "GLM-5.3 Flash does the whole task"},
]
SETUP_LABELS = {s["id"]: s["label"] for s in LIVE_SETUPS} | {
    "takeover28": "v2.8 (Droid)", "takeover29": "v2.9", "takeover30": "v3.0", "takeover31": "v3.1",
    "takeover33": "v3.3", "takeover34": "v3.4", "takeover35": "v3.5", "takeover36": "v3.6", "takeover37": "v3.7",
    "takeover38": "v3.8", "takeover310": "v3.10", "main": "v3.10", "glm": "GLM alone (Droid)",
    "opus": "Opus alone (Droid)", "sonnet": "Sonnet alone", "haiku": "Haiku alone",
    "glmreview": "v2 review", "glmreview25": "v2.5", "glmreview26": "v2.6", "glmreview27": "v2.7",
}


def setup_label(setup: str) -> str:
    return SETUP_LABELS.get(setup, setup)


def split_model(model_id: str) -> tuple[str, str, str]:
    """'ocx/zai/glm-5.3-flash' -> ('ocx', 'zai', 'glm-5.3-flash'); 'droid/claude-opus-5-5' -> ('droid', '', ...)."""
    parts = (model_id or "").split("/")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[-1]
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return "", "", parts[0]


def provider_of(model_id: str) -> str:
    backend, provider, name = split_model(model_id)
    if provider:
        return provider
    if name.startswith("claude-"):
        return "anthropic"
    if name.startswith("glm-"):
        return "zai"
    if name.startswith("gpt-"):
        return "openai"
    return backend or "unknown"


def model_label(model_id: str) -> str:
    """'ocx/zai/glm-5.3-flash' -> 'GLM-5.3 Flash'; 'ocx/anthropic/claude-opus-5-5' -> 'Opus 5.5'."""
    _, _, name = split_model(model_id)
    if not name:
        return "unknown model"
    name = name.removeprefix("claude-")
    m = re.fullmatch(r"(glm|gpt)-([\d.]+)(?:-(.+))?", name)
    if m:
        tail = " ".join(WORDS.get(w, w.capitalize()) for w in (m.group(3) or "").split("-") if w)
        return f"{WORDS[m.group(1)]}-{m.group(2)}" + (f" {tail}" if tail else "")
    words = []
    for w in re.split(r"[-_]", name):
        words.append(w if re.fullmatch(r"\d+(\.\d+)*", w) else WORDS.get(w, w.capitalize()))
    text = " ".join(words)
    return re.sub(r"(\d) (\d)", r"\1.\2", text)


def model_info(model_id: str) -> dict:
    backend, _, _ = split_model(model_id)
    prov = provider_of(model_id)
    return {"id": model_id, "label": model_label(model_id), "provider": prov,
            "provider_label": PROVIDERS.get(prov, prov), "backend": BACKENDS.get(backend, backend)}
