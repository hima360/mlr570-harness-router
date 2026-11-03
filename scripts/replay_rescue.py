"""Offline replay: would a "rescue odds" question to the judge have told good takeovers from hopeless ones?

For every finished takeover run under the given job prefixes, rebuild what the judge knew at handover (the task,
the builder's exit and summary, the judge's problems) and ask the judge model for the probability that a takeover
of the given size makes the task's tests pass. Prints one line per run and the solve rate / takeover spend above and
below each threshold. Nothing runs in a task container and no hidden test is read.

  .venv/bin/python scripts/replay_rescue.py dv8 dv13 > jobs/replay_rescue.dev.txt
"""
import concurrent.futures as cf
import glob
import json
import re
import sys

sys.path.insert(0, ".")
from phase2 import droid  # noqa: E402
from phase2.list_prices import call_usd  # noqa: E402
from phase2.results import collect  # noqa: E402

MODEL = "droid/claude-opus-5-5"
PROMPT = """You reviewed a cheaper engineer's attempt at a coding task and found problems. Before handing the task to a
senior engineer for up to 60 commands in the same container, estimate the odds that the hand-over ends with the
task's hidden tests all passing. Judge from the task size, how far the attempt got and how deep the problems are.

TASK:
{task}

CHEAPER ENGINEER: ended with {exit}. Their summary: {summary}

PROBLEMS YOU FOUND:
{problems}

Reply with one line only: P=<probability between 0 and 1>"""


def handover(trace: str) -> dict | None:
    info = {"takeover_usd": 0.0}
    for line in open(trace):
        e = json.loads(line)
        if e["event"] == "plan":
            info["task"] = e["steps"][0]["goal"]
        elif e["event"] == "senior_review" and "problems" not in info:
            info["problems"] = e.get("problems") or []
            info["exit"] = e.get("builder_exit", "?")
        elif e["event"] == "attempt" and "summary" not in info:
            info["summary"] = e.get("summary", "")
        elif e["event"] == "llm_call" and e.get("role") == "takeover":
            info["takeover_usd"] += call_usd(e["model"], e.get("input_tokens", 0), e.get("cached_tokens", 0),
                                             e.get("output_tokens", 0)) or 0
        elif e["event"] == "takeover" and e.get("phase") == "start":
            info["took_over"] = True
    return info if info.get("took_over") and "task" in info else None


def ask(info: dict) -> tuple[float | None, float]:
    problems = "\n".join(f"- {p.get('what', p) if isinstance(p, dict) else p}" for p in info["problems"])
    text, usage = droid.complete(MODEL, [{"role": "user", "content": PROMPT.format(
        task=info["task"][:6000], exit=info["exit"], summary=info.get("summary", "")[:1500], problems=problems)}],
        effort="low", timeout=300)
    m = re.search(r"P\s*=\s*([01](?:\.\d+)?)", text)
    usd = call_usd(MODEL, usage.get("prompt_tokens", 0), (usage.get("prompt_tokens_details") or {}).get(
        "cached_tokens", 0), usage.get("completion_tokens", 0)) or 0
    return (float(m.group(1)) if m else None), usd


def main(prefixes: list[str]) -> None:
    runs = []
    for p in prefixes:
        for r in collect(".", p):
            tr = glob.glob(f"jobs/{r['name']}/*/agent/trace.jsonl")
            if r["status"] == "done" and tr and (info := handover(tr[0])):
                runs.append((r, info))
    with cf.ThreadPoolExecutor(6) as ex:
        answers = list(ex.map(lambda ri: ask(ri[1]), runs))
    rows = [(p, r["reward"] == 1, info["takeover_usd"], r["name"]) for (r, info), (p, _) in zip(runs, answers)]
    for p, ok, usd, name in sorted(rows, key=lambda x: (x[0] is None, x[0] or 0)):
        print(f"{name:50} P={p} {'SOLVED' if ok else 'fail'} takeover=${usd:.2f}")
    print(f"replay cost ${sum(u for _, u in answers):.2f}")
    for t in (0.1, 0.2, 0.3, 0.4, 0.5):
        lo = [x for x in rows if x[0] is not None and x[0] < t]
        print(f"skip P<{t}: skips {len(lo)} takeovers, loses {sum(x[1] for x in lo)} solves, saves "
              f"${sum(x[2] for x in lo):.2f}")


if __name__ == "__main__":
    main(sys.argv[1:])
