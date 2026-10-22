"""Harness settings: model tiers, prices, routing policy and limits.

Model ids are opencodex ids (`provider/model`). The Sonnet 5.5, Opus 5.5 and Sol ids are
unconfirmed until `ocx models list` shows them; edit `configs/phase2.json`, not this file.
"""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Tier 0-2 are what the router picks; tier 3 is reached only by escalation (and the planner).
DEFAULT_TIERS = [
    "zai/glm-5.3-flash",
    "gpt-6.1-sol",
    "anthropic/claude-sonnet-5-5",
    "anthropic/claude-opus-5-5",
]

# USD per million tokens. Opus 5.5 is a placeholder: check the provider pricing page.
DEFAULT_PRICES = {
    "zai/glm-5.3-flash": {"input": 0.15, "cached": 0.0, "output": 0.50},
    "gpt-6.1-sol": {"input": 2.0, "cached": 0.0, "output": 10.0},
    "anthropic/claude-sonnet-5-5": {"input": 2.0, "cached": 0.10, "output": 10.0},
    "anthropic/claude-opus-5-5": {"input": 5.0, "cached": 0.50, "output": 25.0},
}


@dataclass
class HarnessConfig:
    tiers: list[str] = field(default_factory=lambda: list(DEFAULT_TIERS))
    planner_model: str = "anthropic/claude-opus-5-5"
    # cascade | laya | arch | random | fixed:<tier>
    policy: str = "cascade"
    confidence_threshold: float = 0.35
    base_url: str = "http://127.0.0.1:10100/v1"
    api_key: str = "local"
    prices_per_million: dict = field(default_factory=lambda: dict(DEFAULT_PRICES))
    max_plan_steps: int = 8
    max_parallel: int = 3
    worker_step_limit: int = 40
    worker_command_timeout: int = 180
    check_timeout: int = 300
    random_seed: int = 570
    # `droid/` models are billed in Factory Standard Credits: cost_usd = factory_credits x usd.
    # usd None = no USD rate known, so droid calls add 0 to cost_usd and credits are reported alone.
    factory_credit: dict = field(default_factory=lambda: {"usd": None, "source": "", "checked": ""})

    @property
    def credits_usd(self) -> float | None:
        usd = (self.factory_credit or {}).get("usd")
        return None if usd is None else float(usd)

    @property
    def max_route_tier(self) -> int:
        return len(self.tiers) - 2

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(path: str | Path | None = None, **overrides) -> HarnessConfig:
    data = json.loads(Path(path).read_text()) if path else {}
    data.update({k: v for k, v in overrides.items() if v is not None})
    known = HarnessConfig.__dataclass_fields__
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    cfg = HarnessConfig(**data)
    if isinstance(cfg.confidence_threshold, str):
        cfg.confidence_threshold = float(cfg.confidence_threshold)
    from .droid import is_droid

    missing = [m for m in cfg.tiers + [cfg.planner_model]
               if not is_droid(m) and m not in cfg.prices_per_million]
    if missing:
        raise ValueError(f"no price for models: {missing}")
    return cfg
