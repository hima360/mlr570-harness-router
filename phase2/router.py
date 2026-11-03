"""Router: Laya scores each step's difficulty; Arch-Router decides when Laya is unsure.

Laya answers a `score` question about the step (never about model names) and returns an
expected tier plus a confidence = 1 - entropy/log(k). Below `confidence_threshold` the cascade
defers to Arch-Router (likelihood over three route names). Both load lazily on first use.
"""
import hashlib
import json
import math
import time
from pathlib import Path
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
    router: str = ""  # who actually decided: the scorer model, or a policy that uses no model

    def to_dict(self) -> dict:
        return asdict(self)


class LayaScorer:
    name = "Laya (laya_mlx, local MLX)"

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

    name = "Arch-Router-1.5B (katanemo, local)"

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


D1_TIER_QUESTION = {"tier": {
    "type": "score", "instructions": "How capable must a coding agent be to finish this step correctly?",
    "criteria": ["a small fast model is enough (a simple edit, writing up known results, formatting)",
                 "a strong mid-size model is needed (a multi-file change, debugging, careful testing)",
                 "only a frontier model will do (deep reasoning, reverse engineering, subtle concurrency)"]}}


class D1Scorer:
    """Liquid AI's d1-2B decision model (LFM3, 1.92B), as a second small local router beside Laya. It runs in
    its own venv as a child process (phase2.d1_server) that loads the model once; each step costs one call,
    50-270 ms on this Mac's GPU after a ~2 min first load (measured 2026-10-28)."""

    name = "d1-2B (Liquid AI, local)"

    def __init__(self, python: str = ".venv-d1/bin/python", model: str = "LiquidAI/d1-2B", revision: str = ""):
        self.python, self.model, self.revision = python, model, revision
        self._proc = None

    def _start(self):
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        argv = [self.python if Path(self.python).is_absolute() else str(root / self.python), "-m", "phase2.d1_server",
                "--model", self.model] + (["--revision", self.revision] if self.revision else [])
        self._proc = subprocess.Popen(argv, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, bufsize=1)
        ready = json.loads(self._proc.stdout.readline() or "{}")
        if not ready.get("ready"):
            raise RuntimeError(f"d1 server did not start ({argv[0]}); see phase2/d1_server.py")
        del sys

    def ask(self, text: str, questions: dict) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            self._start()
        self._proc.stdin.write(json.dumps({"text": text, "questions": questions}) + "\n")
        self._proc.stdin.flush()
        reply = json.loads(self._proc.stdout.readline())
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply["answers"]

    def __call__(self, text: str) -> tuple[float, float]:
        """Expected level (0..2) from d1's probabilities, and its confidence."""
        a = self.ask(text, D1_TIER_QUESTION)["tier"]
        probs = a.get("probabilities") or {}
        score = sum(int(k) * float(v) for k, v in probs.items()) if probs else float(a.get("score", 1))
        return score, float(a.get("confidence", 0.0))


class Router:
    """policy: planner | d1 | trained | cascade | laya | arch | random | fixed:<tier>. Scorers are injectable for tests."""

    def __init__(self, cfg, laya=None, arch=None, d1=None):
        self.cfg = cfg
        self.d1 = d1 or D1Scorer(cfg.d1_python, cfg.d1_model, cfg.d1_revision)
        self.laya = laya or LayaScorer()
        self.arch = arch or ArchScorer()
        self.max_tier = cfg.max_route_tier

    def _trained(self, text: str) -> float:
        """Expected strength class (0..2) from the trained head; `self.head_fn` is injectable for tests."""
        if getattr(self, "head_fn", None) is None:
            import numpy as np

            from .train_head import laya_features, predict_proba

            head = json.loads(Path(self.cfg.laya_head).read_text())["model"]
            self.head_fn = lambda t: float((predict_proba(head, laya_features([t]))[0] * np.arange(3)).sum())
        return self.head_fn(text)

    def _clip(self, score: float) -> int:
        """Map a difficulty score on Laya/Arch's 0..2 scale onto the routable tiers 0..max_tier."""
        # Round half up: Python's round() is banker's rounding, which would send 0.5 down but 1.5 up.
        scaled = math.floor(score / 2 * self.max_tier + 0.5)
        return int(min(max(scaled, 0), self.max_tier))

    def route(self, step_id: str, text: str, hint: int | None = None) -> RouteDecision:
        """`hint`: the planner's difficulty rating (0..2) for this step, used by the planner policies."""
        p = self.cfg.policy
        t0 = time.perf_counter()
        if p == "planner" and hint is not None:  # the planner, who read the code, distributes the work
            if hint >= 2 and self.cfg.planner_top_tier:
                # 2026-10-27: a step rated hard went to Sonnet, passed its own weak check, never escalated, and
                # missed the one model (Opus) that solved the task; hard steps may start on the top tier.
                return RouteDecision(len(self.cfg.tiers) - 1, "planner", float(hint), None, 0.0,
                                     "planner rated it hard: top tier")
            return RouteDecision(self._clip(hint), "planner", float(hint), None, 0.0, "planner's difficulty rating")
        if p == "planner":
            p = "laya"  # no rating: fall back to Laya
        if p == "d1":  # Liquid AI's d1-2B, zero-shot
            s, conf = self.d1(text)
            return RouteDecision(self._clip(s), "d1", s, conf, (time.perf_counter() - t0) * 1e3, _name(self.d1))
        if p == "trained":  # Laya features through the head trained by phase2.train_head (cfg.laya_head)
            s = self._trained(text)
            return RouteDecision(self._clip(s), "trained", s, None, (time.perf_counter() - t0) * 1e3,
                                 f"trained head on Laya ({self.cfg.laya_head})")
        if p.startswith("fixed:"):
            tier = int(p.split(":", 1)[1])
            if not 0 <= tier < len(self.cfg.tiers):
                raise ValueError(f"fixed tier {tier} out of range")
            return RouteDecision(tier, "fixed", router=f"fixed policy (tier {tier}, no model)")
        if p == "random":
            h = hashlib.sha256(f"{self.cfg.random_seed}:{step_id}:{text}".encode()).digest()
            return RouteDecision(h[0] % (self.max_tier + 1), "random", router="random policy (no model)")
        ms = lambda: (time.perf_counter() - t0) * 1e3  # noqa: E731
        if p == "arch":
            s = self.arch(text)
            return RouteDecision(self._clip(s), "arch", s, None, ms(), _name(self.arch))
        if p not in ("laya", "cascade"):
            raise ValueError(f"unknown policy {p!r}")
        s, conf = self.laya(text)
        if p == "laya" or conf >= self.cfg.confidence_threshold:
            return RouteDecision(self._clip(s), "laya", s, conf, ms(), _name(self.laya))
        try:
            a = self.arch(text)
        except ImportError:
            return RouteDecision(self._clip(s), "laya (arch unavailable)", s, conf, ms(),
                                 f"{_name(self.laya)}; Arch-Router fallback not installed")
        return RouteDecision(self._clip(a), "arch", a, conf, ms(),
                             f"{_name(self.arch)} (Laya conf {conf:.2f} < {self.cfg.confidence_threshold})")


def _name(scorer) -> str:
    return getattr(scorer, "name", None) or type(scorer).__name__
