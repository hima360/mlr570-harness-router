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
    prices_source: str = ""  # where and when the list prices were checked
    # CLI models only: model id -> effort level (droid -r / claude --effort). Missing = CLI default.
    reasoning_effort: dict = field(default_factory=dict)
    # Per CLI call. The slowest healthy call seen took 41 s; a stalled provider stream hung for 10 min.
    cli_timeout_sec: int = 180
    # Per worker step attempt. step_idle_sec ends it only when no model reply came for that long (a slow
    # but working model keeps going); step_wall_time_sec is a hard safety cap. Either way the verifier
    # then fails the step and it escalates, with the attempt's notes handed over.
    step_idle_sec: int = 600
    step_wall_time_sec: int = 3600
    # Long command outputs are cut to head + tail: every CLI call re-sends the whole conversation,
    # and raw dumps made deepseek turns take 5-14 min.
    max_observation_chars: int = 10000
    max_plan_steps: int = 8
    # Path to an earlier run's trace.jsonl (or its trial / agent dir): reuse that plan instead of calling
    # the planner, so routing conditions are compared on the same DAG.
    plan_from: str = ""
    # Error handler: one fresh call per failed step (no shell, picks one action from a fixed menu).
    # "" = off: a failed step escalates once, as before. Capped at max_interventions per run.
    fixer_model: str = ""
    max_interventions: int = 2
    # CLI workers: once a conversation outgrows this, older turns are folded into PROGRESS SO FAR.
    # 0 = off. ~32k chars is ~11-13k tokens with CLI overhead (measured 2.7-3.3 chars/token).
    context_budget_chars: int = 32000
    # claude/ workers: CLAUDE_CODE_MAX_OUTPUT_TOKENS per call (0 = CLI default). Droid has no such flag.
    max_output_tokens: int = 8000
    # Submit gate: times per attempt a submit is sent back because the step's check failed (0 = off).
    submit_checks: int = 2
    # CLI workers keep one session per attempt and send only new turns, so the provider caches the prefix.
    # The session restarts from the compacted view (context_budget_chars) once it passes this size. 0 = off.
    session_budget_chars: int = 80000
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
    # Harbor --ak values may arrive as strings.
    for name, f in known.items():
        value = getattr(cfg, name)
        if f.type in (int, float, "int", "float") and isinstance(value, str):
            setattr(cfg, name, (int if f.type in (int, "int") else float)(value))
    from .droid import is_droid

    missing = [m for m in cfg.tiers + [cfg.planner_model] + ([cfg.fixer_model] if cfg.fixer_model else [])
               if not is_droid(m) and m not in cfg.prices_per_million]
    if missing:
        raise ValueError(f"no price for models: {missing}")
    return cfg
