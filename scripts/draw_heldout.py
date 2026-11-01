"""Draw held-out Terminal-Bench tasks at random, from tasks the project has never run.

  python scripts/draw_heldout.py --seed 571 --n 6

Never run = no job under jobs/ (or jobs/_excluded_*) names the task, plus --used. Filters, fixed before the draw
(2026-10-29): no GPU, at most 4 CPUs and 8 GB, text only (no Media/Hardware), expert estimate up to 8 hours.
The first draw (ho1/ho2) was seed 570 with the 15 dev-set tasks as the only used ones: --no-jobs --used <those 15>.
"""
import argparse
import os
import random
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = Path(os.path.expanduser("~/Developer/harbor-datasets/terminal-bench"))


def eligible(dataset: Path) -> list[str]:
    out = []
    for t in sorted(os.listdir(dataset)):
        p = dataset / t / "task.toml"
        if not p.exists():
            continue
        c = tomllib.loads(p.read_text())
        env, md = c.get("environment", {}), c.get("metadata", {})
        if env.get("gpus") or (env.get("cpus") or 0) > 4 or (env.get("memory_mb") or 0) > 8192:
            continue
        if md.get("category") in ("Media", "Hardware") or (md.get("expert_time_estimate_hours") or 99) > 8:
            continue
        out.append(t)
    return out


def run_tasks(jobs: Path, tasks: list[str]) -> set[str]:
    names = [p.name for p in jobs.glob("*")] + [p.name for p in jobs.glob("_excluded_*/*")]
    return {t for t in tasks if any(f"-{t}" in n for n in names)}


def main(argv=None) -> list[str]:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--used", nargs="*", default=[])
    ap.add_argument("--no-jobs", action="store_true", help="do not exclude tasks found under jobs/")
    ap.add_argument("--dataset", type=Path, default=DATASET)
    a = ap.parse_args(argv)
    elig = eligible(a.dataset)
    used = set(a.used) | (set() if a.no_jobs else run_tasks(ROOT / "jobs", elig))
    pool = [t for t in elig if t not in used]
    pick = sorted(random.Random(a.seed).sample(pool, a.n))
    print(f"{len(pool)} eligible never-run tasks (seed {a.seed}); pick: {' '.join(pick)}")
    return pick


if __name__ == "__main__":
    main()
