"""v2.7: decide how to work before working. One call to a strong model reads the task and picks a work mode; the
worker then gets that mode's playbook (fixed, written here), the task-specific brief the model wrote (what must exist
at the end, the stated targets, the main risks) and work habits that hold for every task.

Why (2026-11-01, held-out tests 1 and 2): runs failed less from lack of skill than from how they worked: they ran out
of commands mid-change with the required output never written or left half-written, spent their budget checking
without a plan, or worked out exact answers once without cross-checking. The modes are kinds of work, not tasks.
"""
import json
import re

MODES = {
    "build": ("Build or fix software",
              "Read the relevant code and the spec first. Make the smallest change that meets the spec, then run it "
              "end to end the way a user or the task's tests would (fresh process, real entry point, the named edge "
              "cases). Keep behaviour that already worked working."),
    "experiment": ("Reach a measurable target by experimenting",
                   "First write one script that measures the target metric exactly as the task defines it, and get a "
                   "baseline. Then change one thing at a time, re-measure, keep the best version in place, and note "
                   "each result in your notes. Try cheap, small runs before long ones and give long runs a timeout. "
                   "Stop when the target is met with margin; if the budget runs low, leave the best version in place."),
    "answer": ("Work out exact answers",
               "The task wants exact values, positions, names or identities derived from data and domain knowledge. "
               "Read the input formats and conventions carefully (numbering from 0 or 1, units, strands, reference "
               "versions). Compute with code, not by hand. Check every answer a second, independent way (another "
               "method, a worked example, a sanity bound) and settle any disagreement before writing. Write the "
               "output in exactly the format asked."),
    "operate": ("Bring a running system to a required state",
                "Inspect before changing anything: find the services, endpoints or tools, read their docs and the "
                "current state, and plan the whole sequence of changes. Make each change once, confirm it took "
                "effect, and keep a log of what you changed. Finish by checking the final state against every "
                "requirement, not by trusting your log."),
    "multi": ("Several separate deliverables",
              "List the deliverables. Do the ones others depend on first, and finish each end to end, checked, "
              "before starting the next, so that whatever is done is complete when the budget ends."),
}

HABITS = """
HOW TO WORK (all tasks):
- Deliver first: get a complete, valid version of every required output in place early, even if rough, then improve
  it. Never leave a required output missing or half-written between commands: build changes in a copy and swap it in
  once it works. When a limit ends the work, what is in place at that moment is what gets graded.
- Budget: you have {commands} commands and {minutes} minutes. Spend at most a quarter of them on checking; do not
  re-check what you already checked.
- Before you submit, check each requirement and target the task states, the way its tests would, and end your submit
  message with one line per target: target -> what you measured -> the command that measured it."""

TRIAGE_PROMPT = """You are the lead engineer. Before anyone starts on the task below, decide how it should be worked.

TASK:
{task}

Pick the one work mode that fits best:
{modes}

Then reply with only JSON:
{{"mode": "<one of: {names}>", "why": "<one sentence>",
"deliverables": ["<each output that must exist at the end: file path, service state or answer, and its exact format>"],
"targets": ["<each measurable requirement the tests will likely check: threshold, exact match, limit>"],
"risks": ["<at most 3 ways this task is easy to get wrong>"]}}
Use only what the task says; do not invent requirements."""

REVIEW_FOCUS = {
    "build": "Run or read the entry point the way the tests will; check the named edge cases and that old behaviour still works.",
    "experiment": "Check the metric was measured the way the task defines it, on the final version that is in place, "
                  "and that it meets the target with margin.",
    "answer": "Re-derive at least two of the key answers yourself with read-only commands and compare; check the "
              "conventions (numbering, units, reference versions) and the exact output format.",
    "operate": "Check the final state of the system itself, not the colleague's log of what they did.",
    "multi": "Check that every deliverable exists and is complete, not just the first ones.",
}


def triage_prompt(task: str) -> str:
    modes = "\n".join(f"- {k}: {title}" for k, (title, _) in MODES.items())
    return TRIAGE_PROMPT.format(task=task, modes=modes, names=", ".join(MODES))


def parse_strategy(reply: str) -> dict:
    """The model's choice; an unknown mode or unreadable reply falls back to `build` with an empty brief."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    mode = d.get("mode") if d.get("mode") in MODES else "build"
    lists = {k: [str(x) for x in (d.get(k) or []) if str(x).strip()][:8] for k in ("deliverables", "targets", "risks")}
    return {"mode": mode, "why": str(d.get("why") or ""), **lists, "parsed": bool(d)}


def strategy_text(s: dict, commands: int, minutes: int) -> str:
    """What the worker reads after its goal: the mode's playbook, the brief, the habits."""
    title, playbook = MODES[s["mode"]]
    out = [f"\n\nWORK MODE: {title}. {playbook}"]
    for key, head in (("deliverables", "MUST EXIST AT THE END"), ("targets", "TARGETS THE TESTS WILL CHECK"),
                      ("risks", "EASY TO GET WRONG")):
        if s.get(key):
            out.append(f"{head}:\n" + "\n".join(f"- {x}" for x in s[key]))
    out.append(HABITS.format(commands=commands or "a limited number of", minutes=minutes or "limited"))
    return "\n".join(out)


def choose_strategy(ask, task: str) -> dict:
    """`ask(messages) -> reply`. Never raises: a failed call means mode `build` with no brief."""
    try:
        return parse_strategy(ask([{"role": "user", "content": triage_prompt(task)}]))
    except Exception as e:  # noqa: BLE001 - the work goes on without a brief
        return {**parse_strategy(""), "error": f"{type(e).__name__}: {e}"[:200]}
