#!/usr/bin/env python3
"""Draw the shared task plan once, with a fixed seed, and write plan.tsv.

Usage:  python3 make_plan.py [--tb 30] [--swe 6] [--variance 3]

Inputs (one task per line, committed to tasks/):
  tasks/tb4_all.txt         every Terminal-Bench 4.0 task id
  tasks/swe_pro_public.txt  every SWE-Bench Pro public task id, then a tab, then its repository
Output: plan.tsv rows `benchmark  task_id  run_index`. Commit it once and never edit it.
"""
import argparse, random
from collections import defaultdict
from pathlib import Path

SEED = 570
ap = argparse.ArgumentParser()
ap.add_argument("--tb", type=int, default=30)
ap.add_argument("--swe", type=int, default=6)
ap.add_argument("--variance", type=int, default=3, help="TB tasks rerun 2 extra times")
args = ap.parse_args()
rng = random.Random(SEED)


def lines(path):
    return sorted(l.strip() for l in Path(path).read_text().splitlines() if l.strip())


tb = rng.sample(lines("tasks/tb4_all.txt"), args.tb)

# Spread the SWE sample across repositories: round-robin over shuffled repos.
by_repo = defaultdict(list)
for row in lines("tasks/swe_pro_public.txt"):
    task, repo = row.split("\t")
    by_repo[repo].append(task)
repos = sorted(by_repo)
rng.shuffle(repos)
for r in repos:
    rng.shuffle(by_repo[r])
swe = []
while len(swe) < args.swe and any(by_repo.values()):
    for r in repos:
        if by_repo[r] and len(swe) < args.swe:
            swe.append(by_repo[r].pop())

rows = [("tb4", t, 1) for t in tb] + [("swe", t, 1) for t in swe]
rows += [("tb4", t, run) for t in tb[:args.variance] for run in (2, 3)]
Path("plan.tsv").write_text(
    f"# seed {SEED}, tb {args.tb}, swe {args.swe}, variance {args.variance}\n"
    + "".join(f"{b}\t{t}\t{r}\n" for b, t, r in rows))
print(f"plan.tsv: {len(rows)} rows per harness x model")
