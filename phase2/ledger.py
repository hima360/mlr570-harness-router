"""Token and cost accounting for every model call, filled into Harbor's AgentContext."""
import threading
from dataclasses import dataclass


@dataclass
class Usage:
    input_tokens: int = 0  # includes cached tokens, as Harbor counts them
    cached_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0


class Ledger:
    def __init__(self, prices_per_million: dict):
        self.prices = prices_per_million
        self.by_model: dict[str, Usage] = {}
        self._lock = threading.Lock()

    def cost(self, model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
        p = self.prices[model]
        uncached = max(input_tokens - cached_tokens, 0)
        return (uncached * p["input"] + cached_tokens * p["cached"] + output_tokens * p["output"]) / 1e6

    def add(self, model: str, input_tokens: int, cached_tokens: int = 0, output_tokens: int = 0) -> float:
        c = self.cost(model, input_tokens, cached_tokens, output_tokens)
        with self._lock:
            u = self.by_model.setdefault(model, Usage())
            u.input_tokens += input_tokens
            u.cached_tokens += cached_tokens
            u.output_tokens += output_tokens
            u.cost_usd += c
            u.calls += 1
        return c

    def add_from_usage(self, model: str, usage) -> float:
        """Accept an OpenAI-style usage object or dict (prompt/completion tokens + cache details)."""
        get = (lambda k, d=None: usage.get(k, d)) if isinstance(usage, dict) else (
            lambda k, d=None: getattr(usage, k, d))
        details = get("prompt_tokens_details") or {}
        cached = (details.get("cached_tokens") if isinstance(details, dict)
                  else getattr(details, "cached_tokens", 0)) or 0
        return self.add(model, int(get("prompt_tokens", 0) or 0), int(cached),
                        int(get("completion_tokens", 0) or 0))

    def totals(self) -> Usage:
        t = Usage()
        for u in self.by_model.values():
            t.input_tokens += u.input_tokens
            t.cached_tokens += u.cached_tokens
            t.output_tokens += u.output_tokens
            t.cost_usd += u.cost_usd
            t.calls += u.calls
        return t

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
