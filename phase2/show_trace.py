"""Print one trial's plan, routing and outcomes (used for the live demo).

  python -m phase2.show_trace jobs/<job>/<trial>/agent      # or any dir holding trace.jsonl
"""
import json
import sys
from pathlib import Path


def render(trace_path: Path) -> str:
    events = [json.loads(l) for l in trace_path.read_text().splitlines() if l.strip()]
    out = []
    for e in events:
        ev = e["event"]
        if ev == "plan":
            out.append(f"PLAN ({len(e['steps'])} steps, fallback={e.get('fallback')})")
            for s in e["steps"]:
                deps = f" after {','.join(s['depends_on'])}" if s["depends_on"] else ""
                out.append(f"  {s['id']}{deps}: {s['goal'][:90]}")
                out.append(f"      writes={s['writes']} check={s['check'][:60]!r}")
        elif ev == "route":
            conf = f" conf={e['confidence']:.2f}" if e.get("confidence") is not None else ""
            score = f" score={e['score']:.2f}" if e.get("score") is not None else ""
            out.append(f"[{e['t']:7.2f}s] ROUTE {e['step']} -> tier {e['tier']} via {e['source']}{score}{conf} "
                       f"({e['ms']:.0f} ms)")
        elif ev == "attempt":
            mark = "PASS" if e["passed"] else "FAIL"
            out.append(f"[{e['t']:7.2f}s] {mark} {e['step']} attempt {e['attempt']} on {e['model']} "
                       f"({e['n_calls']} calls, {e['exit_status']})")
        elif ev == "escalate":
            out.append(f"[{e['t']:7.2f}s] ESCALATE {e['step']} tier {e['from_tier']} -> {e['to_tier']}")
        elif ev == "done":
            out.append(f"DONE policy={e['policy']} escalations={e['n_escalations']} "
                       f"failed_steps={e['n_failed_steps']}")
    return "\n".join(out)


if __name__ == "__main__":
    d = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(render(d / "trace.jsonl" if d.is_dir() else d))
