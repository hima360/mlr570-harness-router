"""Token and cost accounting for every model call, filled into Harbor's AgentContext.

API models cost tokens x the list price in config. `droid/` models are billed by Factory in
Standard Credits, so their cost is factory_credits x `credits_usd` (USD per credit); with no
rate set their cost stays 0 and only the credits are reported.
"""
import threading
from dataclasses import dataclass

from .droid import is_droid


@dataclass
class Usage:
    input_tokens: int = 0  # includes cached tokens, as Harbor counts them
    cached_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    factory_credits: float = 0.0
    claude_code_cost_usd: float = 0.0  # Claude Code's own estimate, reference only (not the bill)


class Ledger:
    def __init__(self, prices_per_million: dict, credits_usd: float | None = None):
        self.prices = prices_per_million
        self.credits_usd = credits_usd
        self.by_model: dict[str, Usage] = {}
        self._lock = threading.Lock()

    def cost(self, model: str, input_tokens: int, cached_tokens: int, output_tokens: int,
             factory_credits: float | None = None) -> float:
        if is_droid(model):
            return float(factory_credits or 0) * (self.credits_usd or 0.0)
        p = self.prices[model]
        uncached = max(input_tokens - cached_tokens, 0)
        return (uncached * p["input"] + cached_tokens * p["cached"] + output_tokens * p["output"]) / 1e6

    def add(self, model: str, input_tokens: int, cached_tokens: int = 0, output_tokens: int = 0,
            factory_credits: float | None = None, claude_code_cost_usd: float | None = None) -> float:
        c = self.cost(model, input_tokens, cached_tokens, output_tokens, factory_credits)
        with self._lock:
            u = self.by_model.setdefault(model, Usage())
            u.input_tokens += input_tokens
            u.cached_tokens += cached_tokens
            u.output_tokens += output_tokens
            u.cost_usd += c
            u.calls += 1
            u.factory_credits += float(factory_credits or 0)
            u.claude_code_cost_usd += float(claude_code_cost_usd or 0)
        return c

    def add_from_usage(self, model: str, usage) -> float:
        """Accept an OpenAI-style usage object or dict (prompt/completion tokens + cache details)."""
        get = (lambda k, d=None: usage.get(k, d)) if isinstance(usage, dict) else (
            lambda k, d=None: getattr(usage, k, d))
        details = get("prompt_tokens_details") or {}
        cached = (details.get("cached_tokens") if isinstance(details, dict)
                  else getattr(details, "cached_tokens", 0)) or 0
        return self.add(model, int(get("prompt_tokens", 0) or 0), int(cached),
                        int(get("completion_tokens", 0) or 0), get("factory_credits"),
                        get("claude_code_cost_usd"))

    def totals(self) -> Usage:
        t = Usage()
        for u in self.by_model.values():
            t.input_tokens += u.input_tokens
            t.cached_tokens += u.cached_tokens
            t.output_tokens += u.output_tokens
            t.cost_usd += u.cost_usd
            t.calls += u.calls
            t.factory_credits += u.factory_credits
        return t

    def credits_report(self) -> dict:
        """Factory credits per droid model plus the USD rate used (None = no rate, cost not counted)."""
        by_model = {m: u.factory_credits for m, u in self.by_model.items() if is_droid(m)}
        return {"factory_credits": by_model, "factory_credits_total": sum(by_model.values()),
                "credits_usd": self.credits_usd}

    def fill_context(self, context) -> None:
        """Write totals and the per-model breakdown onto a Harbor AgentContext."""
        from harbor.models.agent.context import ModelUsage

        t = self.totals()
        context.n_input_tokens = t.input_tokens
        context.n_cache_tokens = t.cached_tokens
        context.n_output_tokens = t.output_tokens
        context.cost_usd = round(t.cost_usd, 6)
        context.model_usage = {
            m: ModelUsage(n_input_tokens=u.input_tokens, n_cache_tokens=u.cached_tokens,
                          n_output_tokens=u.output_tokens, cost_usd=round(u.cost_usd, 6))
            for m, u in self.by_model.items()
        }
        extra = self.credits_report() if any(is_droid(m) for m in self.by_model) else {}
        reported = {m: round(u.claude_code_cost_usd, 6) for m, u in self.by_model.items() if u.claude_code_cost_usd}
        if reported:
            extra["claude_code_cost_usd"] = reported
        if extra:
            context.metadata = {**(context.metadata or {}), **extra}
