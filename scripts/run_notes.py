"""One line per finished harness run (v2.8+): judge verdicts, builder fix, takeover, cost by role, outcome.

  .venv/bin/python scripts/run_notes.py [--new]   (--new: only runs not listed before, tracked in jobs/.run_notes_seen)
"""
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from phase2.list_prices import call_usd  # noqa: E402

seen_f = ROOT / "jobs" / ".run_notes_seen"
seen = set(seen_f.read_text().split()) if seen_f.exists() else set()
new = "--new" in sys.argv
for res in sorted(glob.glob(str(ROOT / "jobs/*-takeover*/*/result.json"))):
    job = res.split("/")[-3]
    if new and job in seen:
        continue
    seen.add(job)
    tr = Path(res).parent / "agent" / "trace.jsonl"
    ev = [json.loads(l) for l in tr.read_text().splitlines() if l.strip()] if tr.exists() else []
    r = json.load(open(res))
    reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    usd = {}
    for e in ev:
        if e.get("event") == "llm_call":
            usd[e.get("role")] = usd.get(e.get("role"), 0) + (call_usd(e.get("model", ""), e.get("input_tokens"),
                                                                       e.get("cached_tokens"), e.get("output_tokens")) or 0)
    rv = [f"{e.get('verdict')}{len(e.get('problems') or [])}" for e in ev if e.get("event") == "senior_review" and "verdict" in e]
    first = next((e.get("builder_exit") for e in ev if e.get("event") == "senior_review" and e.get("round") == 1), "")
    to = next((e for e in ev if e.get("event") == "takeover" and e.get("phase") == "end"), None)
    exc = (r.get("exception_info") or {}).get("exception_type") or ""
    print(f"{job[:48]:48} {'SOLVED' if reward == 1 else 'fail  '} build={first or '-':14} review={','.join(rv) or '-':12} "
          f"takeover={(to or {}).get('exit_status', '-'):14} calls={(to or {}).get('n_calls', '-'):>3} "
          f"${sum(usd.values()):.2f} " + " ".join(f"{k}={v:.2f}" for k, v in sorted(usd.items())) + (f" EXC={exc}" if exc else ""))
seen_f.write_text("\n".join(sorted(seen)))
