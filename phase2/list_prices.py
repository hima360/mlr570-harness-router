"""API list prices, to state every run's cost in dollars even though the runs bill subscription credits.

USD per million tokens, (input, cache write, cache read, output). The traces record, per call, all input tokens
(cache reads and writes included), the cached (read) part and the output; cache writes are not split out, so the
uncached input is priced at the cache-write rate where one exists (slightly over, never under, the real cost).
Checked 2026-10-29: Anthropic and Z.ai from their pricing pages; GPT-6 Luna and GPT-6.1 Sol from secondary
sites (OpenAI's page not reached), and they only appear in a few old setups' small calls.
"""
LIST_PRICES = {
    # https://platform.claude.com/docs/en/about-claude/pricing
    "claude-opus-5-5": (4.00, 5.00, 0.20, 20.00),
    "claude-sonnet-5-5": (2.00, 2.50, 0.10, 10.00),
    "claude-haiku-5-5": (0.10, 0.125, 0.01, 0.50),  # prompts up to 100k tokens
    # https://docs.z.ai/guides/overview/pricing (cached-input storage free for now)
    "glm-5.3-flash": (0.15, 0.15, 0.03, 0.50),
    "glm-5.3": (1.40, 1.40, 0.26, 4.40),  # same page, checked 2026-11-02
    # secondary sources (thepricer.org, anotherwrapper.com)
    "gpt-6-luna": (0.10, 0.125, 0.01, 0.50),
    "gpt-6.1-sol": (2.00, 2.50, 0.20, 10.00),
}


def call_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float | None:
    """List-price cost of one model call, or None for a model with no listed price."""
    p = LIST_PRICES.get(model.split("/")[-1])
    if p is None:
        return None
    fresh = max((input_tokens or 0) - (cached_tokens or 0), 0)
    return (fresh * p[1] + (cached_tokens or 0) * p[2] + (output_tokens or 0) * p[3]) / 1e6


def trace_usd(events) -> float:
    """Sum over a run's llm_call events (models without a price add 0)."""
    return sum(call_usd(e.get("model", ""), e.get("input_tokens"), e.get("cached_tokens"), e.get("output_tokens")) or 0
               for e in events if e.get("event") == "llm_call")
