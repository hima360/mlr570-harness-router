"""Recompute cost_usd in a Phase 1 results file from its token counts and a config's price table.

Only cost_usd changes; every other field and the row order stay as logged (RULES.md 6, exception).
Dry run by default: prints old and new totals. --write rewrites the file in place.

  python scripts/recompute_cost.py results/ammar.jsonl configs/ammar.json [--write]
"""
import argparse
import json
from pathlib import Path

FIELDS = ("input_tokens", "output_tokens", "cached_tokens")


def row_cost(row: dict, prices: dict) -> float | None:
    """Same formula as run_grid.cost_usd; None when the row has no tokens or the model has no price."""
    p = prices.get(row.get("model"))
    if p is None or all(row.get(k) is None for k in FIELDS):
        return None
    return round(sum((row.get(k) or 0) * p[k] / 1e6 for k in FIELDS), 4)


def recompute(lines: list[str], prices: dict) -> tuple[list[str], float, float, int]:
    out, old_total, new_total, changed = [], 0.0, 0.0, 0
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        row = json.loads(line)
        old = row.get("cost_usd")
        new = row_cost(row, prices)
        old_total += old or 0
        if new is None or new == old:
            new_total += old or 0
            out.append(line)
            continue
        row["cost_usd"] = new
        new_total += new
        changed += 1
        out.append(json.dumps(row) + "\n")
    return out, old_total, new_total, changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("config", type=Path)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    prices = json.loads(a.config.read_text())["prices_per_million"]
    lines = a.results.read_text().splitlines(keepends=True)
    out, old_total, new_total, changed = recompute(lines, prices)
    print(f"{a.results}: {changed} of {sum(1 for l in lines if l.strip())} rows change, "
          f"total ${old_total:.2f} -> ${new_total:.2f}")
    if a.write:
        a.results.write_text("".join(out))
        print("written")


if __name__ == "__main__":
    main()
