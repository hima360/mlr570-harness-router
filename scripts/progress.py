"""How far an evaluation is, in a few lines: runs done / running / queued, solves so far, a rough finish time.

  scripts/progress.py            the newest evaluation
  scripts/progress.py ho4        that one
  scripts/progress.py ho4 -w     refresh every 60 s (Ctrl-C to quit)
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phase2.results import prefixes, progress  # noqa: E402


def render(prefix: str) -> str:
    p = progress(ROOT, prefix)
    if not p["rows"] and not p["total"]:
        latest = next((x for x in prefixes(ROOT) if x != prefix), None)
        return f"{prefix}: not started yet." + (f"\n\nLatest evaluation:\n{render(latest)}" if latest else "")
    eta = time.strftime("%H:%M", time.localtime(p["eta"])) if p["eta"] else "-"
    lines = [f"{prefix}: {p['done']}/{p['total']} runs done, {p['running']} running, {p['queued']} queued; "
             f"finish about {eta}"]
    for s, a in p["setups"].items():
        lines.append(f"  {s:12s} solved {a['passed']}/{a['runs']}  tests {a['mean_tests']:.0%}  ${a['usd']:.2f}  "
                     f"{a['tokens'] / 1e6:.1f}M tokens  {a['minutes_per_run']:.0f} min/run")
    for r in p["rows"]:
        if r["status"] == "running":
            lines.append(f"  running: {r['setup']} {r['task']} r{r['repeat']}")
    log = ROOT / "jobs" / f"{prefix}.supervisor.log"
    if log.exists():
        lines.append("  supervisor: " + log.read_text().strip().splitlines()[-1])
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix", nargs="?")
    ap.add_argument("-w", "--watch", action="store_true")
    a = ap.parse_args(argv)
    prefix = a.prefix or (prefixes(ROOT) or ["?"])[0]
    while True:
        out = render(prefix)
        print(("\033[2J\033[H" if a.watch else "") + out, flush=True)
        if not a.watch:
            return
        time.sleep(60)


if __name__ == "__main__":
    main()
