"""Print one trial's plan, routing and outcomes (used for the live demo).

  python -m phase2.show_trace jobs/<job>/<trial>/agent      # or any dir holding trace.jsonl
  python -m phase2.show_trace --follow jobs/<job>           # live view; waits for the trial to start
"""
import json
import sys
import time
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
        elif ev == "llm_call_failed":
            retry = ", retrying" if e.get("will_retry") else ""
            out.append(f"[{e['t']:7.2f}s] CALL FAILED {e.get('role')} {e.get('step', '')} {e.get('model')} "
                       f"{e['kind']} after {e['ms'] / 1000:.0f} s{retry}")
        elif ev == "escalate":
            out.append(f"[{e['t']:7.2f}s] ESCALATE {e['step']} tier {e['from_tier']} -> {e['to_tier']}")
        elif ev == "done":
            out.append(f"DONE policy={e['policy']} escalations={e['n_escalations']} "
                       f"failed_steps={e['n_failed_steps']}")
    return "\n".join(out)


def find_agent_dir(path: Path) -> Path | None:
    """`path` itself if it holds trace.jsonl, else the newest trial agent dir under it."""
    if (path / "trace.jsonl").exists():
        return path
    traces = sorted(path.glob("*/agent/trace.jsonl"), key=lambda p: p.stat().st_mtime)
    return traces[-1].parent if traces else None


def _short(text, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n] + " ..."


def render_worker(worker_path: Path, n_turns: int = 3) -> str:
    """Last few turns of one worker attempt: what the model said and what the command printed."""
    try:
        msgs = json.loads(worker_path.read_text()).get("messages", [])
    except (OSError, json.JSONDecodeError):  # mid-write
        return f"WORKER {worker_path.stem}: (updating)"
    turns = [i for i, m in enumerate(msgs) if m.get("role") == "assistant"]
    out = [f"WORKER {worker_path.stem.removeprefix('worker_')}: {len(turns)} model turns"]
    for i in turns[-n_turns:]:
        out.append(f"  model > {_short(msgs[i].get('content'), 400)}")
        nxt = msgs[i + 1] if i + 1 < len(msgs) else None
        if nxt and nxt.get("role") != "assistant":
            out.append(f"  shell < {_short(nxt.get('content'), 240)}")
    return "\n".join(out)


def render_live(agent_dir: Path) -> str:
    parts = [f"{agent_dir.parent.name}  {time.strftime('%H:%M:%S')}", "", render(agent_dir / "trace.jsonl")]
    workers = sorted(agent_dir.glob("worker_*.json"), key=lambda p: p.stat().st_mtime)
    if workers:
        parts += ["", render_worker(workers[-1])]
    result = agent_dir.parent / "result.json"
    if result.exists():
        r = json.loads(result.read_text())
        reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        err = (r.get("exception_info") or {}).get("exception_type")
        parts += ["", f"RESULT reward={reward}" + (f" exception={err}" if err else "")]
    return "\n".join(parts)


def follow(path: Path, every: float = 2.0) -> None:
    while True:
        agent_dir = find_agent_dir(path)
        screen = render_live(agent_dir) if agent_dir else f"waiting for a trial under {path} ..."
        print("\x1b[2J\x1b[H" + screen, flush=True)
        if agent_dir and (agent_dir.parent / "result.json").exists():
            return
        time.sleep(every)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--follow":
        follow(Path(args[1] if len(args) > 1 else "."))
    else:
        d = Path(args[0] if args else ".")
        print(render(d / "trace.jsonl" if d.is_dir() else d))
