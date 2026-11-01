"""A results page for one or more evaluations, to open in any browser (one file, nothing loaded from the web).

  .venv/bin/python scripts/report_html.py ho1 ho2          -> reports/ho1+ho2.html
  .venv/bin/python scripts/report_html.py ho1 ho2 --setups opus,glm,glmreview,glmreview25
  .venv/bin/python scripts/report_html.py ho4 --title "v2.6 held-out" --labels glmreview26=v2.6,glm="GLM alone"

Rows of all the given evaluations are combined (ho1 had Opus alone, ho2 the GLM setups on the same tasks).
"""
import argparse
import html
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phase2.results import by_setup, collect  # noqa: E402

CSS = """
:root { --bg:#f7f5f0; --fg:#14213d; --muted:#5c6779; --line:#d9d3c7; --card:#fffdf8; --good:#2f7d4f;
        --bad:#b84a3a; --accent:#b5651d; --bar:#c98a4b; }
@media (prefers-color-scheme: dark) { :root { --bg:#121722; --fg:#e8e4da; --muted:#9aa3b2; --line:#2c3444;
        --card:#1a2130; --good:#5fbf86; --bad:#e07a68; --accent:#e0a060; --bar:#b07a45; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, -apple-system, sans-serif; }
main { max-width:1100px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:26px; margin:0 0 4px; } h2 { font-size:18px; margin:32px 0 8px; }
.meta, .note { color:var(--muted); font-size:13px; }
.wrap { overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:8px; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
th, td { padding:8px 12px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
tr:last-child td { border-bottom:0; }
tr.best td { font-weight:700; color:var(--good); }
.bars { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; }
.bar { display:flex; align-items:center; gap:8px; margin:6px 0; font-size:13px; }
.bar span:first-child { width:140px; flex:none; overflow:hidden; text-overflow:ellipsis; }
.bar div { height:14px; background:var(--bar); border-radius:3px; min-width:2px; }
.b { display:inline-block; min-width:44px; padding:1px 6px; margin:1px; border-radius:4px; text-align:center;
     font-size:12px; background:color-mix(in srgb, var(--good) calc(var(--f) * 55%), var(--bad) calc(55% - var(--f) * 55%)); color:var(--fg); }
.b.solved { font-weight:700; outline:2px solid var(--good); }
.b.run { background:transparent; outline:1px dashed var(--muted); }
"""


def _money(x: float) -> str:
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"${x:.2f}"


def build(rows: list[dict], title: str, labels: dict[str, str]) -> str:
    e = html.escape
    agg = by_setup(rows)
    name = lambda s: e(labels.get(s, s))
    done = sum(r["status"] == "done" for r in rows)
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{e(title)}</title>"
           f"<style>{CSS}</style></head><body><main><h1>{e(title)}</h1>"
           f"<p class='meta'>Generated {e(time.strftime('%Y-%m-%d %H:%M'))} · {done} runs done, "
           f"{len(rows) - done} running</p>"]
    if not agg:
        out.append("<p>No finished runs yet.</p></main></body></html>")
        return "".join(out)
    best = max(agg.items(), key=lambda kv: (kv[1]["passed"], -kv[1]["usd"]))[0]
    out.append("<h2>By setup</h2><div class='wrap'><table><tr><th>Setup</th><th>Runs</th><th>Solved</th>"
               "<th>Tests passed</th><th>Tokens</th><th>Total cost</th><th>Cost per solve</th><th>Min per run</th></tr>")
    for s, a in agg.items():
        out.append(f"<tr class='{'best' if s == best else ''}'><td>{name(s)}</td><td>{a['runs']}</td>"
                   f"<td>{a['passed']} / {a['runs']}</td><td>{a['mean_tests']:.0%}</td><td>{a['tokens'] / 1e6:.1f}M</td>"
                   f"<td>{_money(a['usd'])}</td><td>{_money(a['usd_per_solve'])}</td><td>{a['minutes_per_run']:.0f}</td></tr>")
    out.append("</table></div><h2>Total cost</h2><div class='bars'>")
    top = max(a["usd"] for a in agg.values()) or 1
    for s, a in agg.items():
        out.append(f"<div class='bar'><span>{name(s)}</span><div style='width:{60 * a['usd'] / top:.1f}%'></div>"
                   f"<span>{_money(a['usd'])}</span></div>")
    out.append("</div><h2>By task</h2><p class='note'>Each badge is one run: share of the task's hidden tests "
               "passed; outlined with ✓ = solved; dashed = still running.</p><div class='wrap'><table><tr><th>Task</th>")
    setups = list(agg) + sorted({r["setup"] for r in rows} - set(agg))
    out += [f"<th>{name(s)}</th>" for s in setups] + ["</tr>"]
    cells: dict = {}
    for r in sorted(rows, key=lambda r: r["repeat"]):
        cells.setdefault(r["task"], {}).setdefault(r["setup"], []).append(r)
    for task in sorted(cells):
        out.append(f"<tr><td>{e(task)}</td>")
        for s in setups:
            badges = []
            for r in cells[task].get(s, []):
                if r["status"] != "done":
                    badges.append("<span class='b run'>run</span>")
                    continue
                f = r["tests"] or 0.0
                solved = r["reward"] == 1
                label = ("✓ " if solved else "") + ("-" if r["tests"] is None else f"{f:.0%}")
                badges.append(f"<span class='b{' solved' if solved else ''}' style='--f:{f:.2f}'>{e(label)}</span>")
            out.append(f"<td>{''.join(badges)}</td>")
        out.append("</tr>")
    out.append("</table></div><p class='note'>Solved = all hidden tests pass (Harbor reward 1). Tests passed = share "
               "of the task's hidden tests that pass. Cost = tokens x API list price; nothing is billed per token."
               "</p></main></body></html>")
    return "".join(out)


def main(argv=None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("prefixes", nargs="+")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--title")
    ap.add_argument("--labels", default="", help="setup=Label,setup2=Label2")
    ap.add_argument("--setups", help="only these setups, in this order (comma list)")
    a = ap.parse_args(argv)
    labels = dict(kv.split("=", 1) for kv in a.labels.split(",") if "=" in kv)
    rows = [r for p in a.prefixes for r in collect(ROOT, p)]
    if a.setups:
        order = a.setups.split(",")
        rows = sorted((r for r in rows if r["setup"] in order), key=lambda r: order.index(r["setup"]))
    out = a.out or ROOT / "reports" / f"{'+'.join(a.prefixes)}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(rows, a.title or f"Evaluation results: {', '.join(a.prefixes)}", labels))
    print(out)
    return out


if __name__ == "__main__":
    main()
