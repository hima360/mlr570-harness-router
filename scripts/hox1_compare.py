"""Held-out on opencodex (hox1, 2026-11-19/20, 18 tasks x 1 run) vs the Droid held-out (2026-11-03, report table).
  python scripts/hox1_compare.py > docs/HOX1_RESULTS.md"""
import glob, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"ocxv41": "v2.8 (opencodex)", "ocxopus": "Opus 5.5 alone (opencodex)"}
DROID = {"v2.8 (Droid, 2 runs/task)": (11, 36, 41.83), "Opus 5.5 alone (Droid, 2 runs/task)": (4, 35, 31.31)}
rows, per_task = {}, {}
for f in sorted(glob.glob(str(ROOT / "jobs/hox1-*/*/result.json"))):
    setup = Path(f).parents[1].name.split("-")[1]
    line = subprocess.run([sys.executable, str(ROOT / "scripts/runline.py"), f], capture_output=True, text=True).stdout
    parts = line.split()
    if len(parts) < 3:
        continue
    task, solved, cost = parts[0], parts[1] in ("reward=1", "reward=1.0"), float(parts[2].lstrip("$"))
    n, s, c = rows.get(setup, (0, 0, 0.0))
    rows[setup] = (n + 1, s + solved, c + cost)
    per_task.setdefault(task, {})[setup] = ("PASS" if solved else "fail", cost)
print(f"# Held-out on opencodex vs Droid (generated {time.strftime('%Y-%m-%d %H:%M')})\n")
print("| setup | solved | runs | total cost | cost per solve | cost per run |\n|---|---|---|---|---|---|")
for k, (s, n, c) in DROID.items():
    print(f"| {k} | {s} | {n} | ${c:.2f} | ${c/s:.2f} | ${c/n:.2f} |")
for k, (n, s, c) in rows.items():
    print(f"| {NAMES.get(k, k)} | {s} | {n} | ${c:.2f} | {'$%.2f' % (c/s) if s else 'n/a'} | ${c/n:.2f} |")
print("\n| task | " + " | ".join(NAMES.get(k, k) for k in NAMES) + " |\n|---|---|---|")
for t, d in sorted(per_task.items()):
    print(f"| {t} | " + " | ".join(f"{d[k][0]} ${d[k][1]:.2f}" if k in d else "-" for k in NAMES) + " |")
