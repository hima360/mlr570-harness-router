"""Router: Laya scores each step's difficulty; Arch-Router decides when Laya is unsure.

Laya answers a `score` question about the step (never about model names) and returns an
expected tier plus a confidence = 1 - entropy/log(k). Below `confidence_threshold` the cascade
defers to Arch-Router (likelihood over three route names). Both load lazily on first use.
"""
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass

TIER_QUESTION = {
    "tier": {
        "type": "score",
        "instructions": "How capable must the engineer be to finish `task` correctly?",
        "criteria": [
            "a junior can do it in a few minutes with one small edit",
            "a solid developer can do it in an hour following clear steps",
            "a senior expert must reason deeply across many files and systems",
        ],
    }
}

ARCH_ROUTES = [
    {"name": "trivial_edit", "description": "A tiny mechanical change to one line or one config value, such as renaming, typos, version bumps"},
    {"name": "standard_feature", "description": "A typical well-scoped feature or test-writing task a solid developer finishes in about an hour"},
    {"name": "hard_engineering", "description": "Deep debugging, system design or large refactors that require expert reasoning across many files and services"},
]
ARCH_TI = '\nYou are a helpful assistant designed to find the best suited route.\nYou are provided with route description within <routes></routes> XML tags:\n<routes>\n\n{routes}\n\n</routes>\n\n<conversation>\n\n{conversation}\n\n</conversation>\n'
ARCH_FP = '\nYour task is to decide which route is best suit with user intent on the conversation in <conversation></conversation> XML tags.  Follow the instruction:\n1. If the latest intent from user is irrelevant or user intent is full filled, response with other route {"route": "other"}.\n2. You must analyze the route descriptions and find the best match route for user latest intent. \n3. You only response the name of the route that best matches the user\'s request, use the exact name in the <routes></routes>.\n\nBased on your analysis, provide your response in the following JSON formats if you decide to match any route:\n{"route": "route_name"} \n'


@dataclass
class RouteDecision:
    tier: int
    source: str  # laya | arch | fixed | random | laya (arch unavailable)
    score: float | None = None
    confidence: float | None = None
    ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class LayaScorer:
    def __init__(self):
        self._agent = None

    def __call__(self, text: str) -> tuple[float, float]:
        if self._agent is None:
            import laya_mlx

            self._agent = laya_mlx.load()
        ans = self._agent.predict({"task": text}, TIER_QUESTION)["answers"]["tier"]
        return float(ans["score"]), float(ans["confidence"])


class ArchScorer:
    """Port of router-bench/run_hf.py `arch`: softmax over summed log-probs of each route name."""

    def __init__(self, device: str = "mps"):
        self.device = device
        self._m = self._tok = None

    def _load(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        name = "katanemo/Arch-Router-1.5B"
        self._tok = AutoTokenizer.from_pretrained(name)
        self._m = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.float16).to(self.device).eval()

    def __call__(self, text: str) -> float:
        import numpy as np
        import torch

        if self._m is None:
            self._load()
        msgs = [{"role": "user", "content": ARCH_TI.format(
            routes=json.dumps(ARCH_ROUTES), conversation=json.dumps([{"role": "user", "content": text}])) + ARCH_FP}]
        base = self._tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False) + '{"route": "'
        lps = []
        for r in ARCH_ROUTES:
            ids = self._tok(base + r["name"], return_tensors="pt").input_ids.to(self.device)
            k = len(self._tok(r["name"]).input_ids)
            with torch.no_grad():
                lo = torch.log_softmax(self._m(ids).logits[0, -k - 1:-1].float(), -1)
            lps.append(float(lo.gather(1, ids[0, -k:, None]).sum()))
        p = np.exp(np.array(lps) - max(lps))
        p /= p.sum()
        return float((p * np.arange(len(ARCH_ROUTES))).sum())


class Router:
    """policy: cascade | laya | arch | random | fixed:<tier>. Scorers are injectable for tests."""

    def __init__(self, cfg, laya=None, arch=None):
        self.cfg = cfg
        self.laya = laya or LayaScorer()
        self.arch = arch or ArchScorer()
        self.max_tier = cfg.max_route_tier

    def _clip(self, score: float) -> int:
        """Map a difficulty score on Laya/Arch's 0..2 scale onto the routable tiers 0..max_tier."""
        # Round half up: Python's round() is banker's rounding, which would send 0.5 down but 1.5 up.
        scaled = math.floor(score / 2 * self.max_tier + 0.5)
        return int(min(max(scaled, 0), self.max_tier))

    def route(self, step_id: str, text: str) -> RouteDecision:
        p = self.cfg.policy
        t0 = time.perf_counter()
        if p.startswith("fixed:"):
            tier = int(p.split(":", 1)[1])
            if not 0 <= tier < len(self.cfg.tiers):
                raise ValueError(f"fixed tier {tier} out of range")
            return RouteDecision(tier, "fixed")
        if p == "random":
            h = hashlib.sha256(f"{self.cfg.random_seed}:{step_id}:{text}".encode()).digest()
            return RouteDecision(h[0] % (self.max_tier + 1), "random")
        if p == "arch":
            s = self.arch(text)
            return RouteDecision(self._clip(s), "arch", s, None, (time.perf_counter() - t0) * 1e3)
        if p not in ("laya", "cascade"):
            raise ValueError(f"unknown policy {p!r}")
        s, conf = self.laya(text)
        if p == "laya" or conf >= self.cfg.confidence_threshold:
            return RouteDecision(self._clip(s), "laya", s, conf, (time.perf_counter() - t0) * 1e3)
        try:
            a = self.arch(text)
        except ImportError:
            return RouteDecision(self._clip(s), "laya (arch unavailable)", s, conf,
                                 (time.perf_counter() - t0) * 1e3)
        return RouteDecision(self._clip(a), "arch", a, conf, (time.perf_counter() - t0) * 1e3)
