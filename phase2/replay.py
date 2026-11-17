"""Offline router replay on Phase 1 logs: no model calls, no subscription usage.

For every task, Phase 1 says whether each model solved it and at what cost (in one reference
harness, run 1). A routing policy picks one model per task; replay reads off pass and cost.

  python -m phase2.replay --harness codex --results results/ [--scores scores.json]

`--scores` is a JSON map {router_name: {task_id: tier}} produced by any router (Laya, Arch,
RouteLLM, NVIDIA, the trained head) so all routers are compared on identical data.
Labels for training = the cheapest tier that solved the task, else the frontier tier.
"""
import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from .config import load_config


def load_grid(results_dir: Path, harness: str, run: int = 1) -> dict:
    """{task: {model: (passed, cost)}} for one harness and run index."""
    grid = defaultdict(dict)
    for f in sorted(results_dir.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("harness") == harness and r.get("run") == run and r.get("passed") is not None:
                grid[r["task"]][r["model"]] = (bool(r["passed"]), float(r.get("cost_usd") or 0.0))
    return dict(grid)


def labels(grid: dict, tiers: list[str]) -> dict:
    """Cheapest Phase 1 tier that solved the task; unsolved -> len(tiers), the frontier
    (escalation) tier that sits just above the Phase 1 models in the harness config."""
    out = {}
    for task, by_model in grid.items():
        solved = [i for i, m in enumerate(tiers) if by_model.get(m, (False, 0))[0]]
        out[task] = solved[0] if solved else len(tiers)
    return out


def evaluate(grid: dict, tiers: list[str], choose) -> dict:
    """choose(task) -> tier. Tasks missing that model's result are skipped and counted."""
    n = solved = 0
    cost = 0.0
    skipped = 0
    for task, by_model in grid.items():
        m = tiers[choose(task)]
        if m not in by_model:
            skipped += 1
            continue
        ok, c = by_model[m]
        n += 1
        solved += ok
        cost += c
    return {"tasks": n, "pass_rate": round(solved / n, 4) if n else None, "cost_usd": round(cost, 4),
            "cost_per_solved": round(cost / solved, 4) if solved else None, "skipped": skipped}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--harness", required=True)
    ap.add_argument("--config")
    ap.add_argument("--scores")
    ap.add_argument("--labels-out")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    tiers = cfg.tiers[:-1]  # Phase 1 models; the escalation tier (Opus) is not in the Phase 1 grid
    grid = load_grid(Path(args.results), args.harness)
    lab = labels(grid, tiers)
    rng = random.Random(cfg.random_seed)
    rand = {t: rng.randrange(len(tiers)) for t in sorted(grid)}
    policies = {f"always:{m}": (lambda t, i=i: i) for i, m in enumerate(tiers)}
    policies["random"] = rand.__getitem__
    policies["oracle"] = lambda t: min(lab[t], len(tiers) - 1)
    if args.scores:
        for name, table in json.loads(Path(args.scores).read_text()).items():
            policies[name] = lambda t, table=table: min(max(int(table.get(t, 0)), 0), len(tiers) - 1)
    report = {name: evaluate(grid, tiers, f) for name, f in policies.items()}
    frontier = report[f"always:{tiers[-1]}"]["pass_rate"] or 0
    for r in report.values():
        r["quality_retained"] = round(r["pass_rate"] / frontier, 4) if frontier and r["pass_rate"] is not None else None
    print(json.dumps(report, indent=2))
    if args.labels_out:
        Path(args.labels_out).write_text(json.dumps(lab, indent=2))
    return report


if __name__ == "__main__":
    main()
