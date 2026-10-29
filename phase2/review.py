"""Senior review: a cheaper model builds, a stronger model reviews the finished work before it is accepted.

2026-10-29: no signal we had could tell when a cheap model's work was wrong. The planner rated every task hard,
independent tests written from the task text passed every near-miss, and a lead model given helpers never used
them on small codebases. Most failures were near misses (0.67-0.90 of the task's tests passed). Here the harness,
not the model, decides: when the worker submits, a stronger model reads the task, the worker's transcript and the
diff of what it changed. It may run a few read-only commands, then lists the concrete problems that would make
the task's own tests fail. The worker gets them back with its context intact and fixes them. One review costs a
few strong-model calls instead of a whole strong-model run.
"""
import json
import re

SENIOR_PROMPT = """You are the senior engineer on a team. A colleague using a cheaper model says the task below is
done. Before it ships, find what would make the task's own hidden tests fail. You do not edit anything.

TASK:
{task}

THEIR REPORT: {report}

WHAT THEY DID (commands and the start of their outputs, newest last):
{digest}

WHAT CHANGED in the work folder (diff against the starting files; large or binary files only listed):
{diff}

Check the work against every requirement the task states: behaviours, scenarios and edge cases it names
(restarts, failures, concurrency, limits, empty or dirty input), exact output formats and paths, performance
limits, and how the tests will run it (possibly as a non-root user, in a fresh shell, without the services the
colleague started). Look for requirements they skipped, misread or only half met, and for claims their
transcript does not back up. When the task sets several targets that pull against each other (for example
precision and recall, speed and correctness), weigh them all: never ask for a change that fixes one target by
breaking another, and say in the fix how to keep both.
{explore}
Then reply with only JSON: {{"verdict": "ship" | "fix", "problems": [{{"what": "<the concrete problem>",
"evidence": "<the task text or output that shows it>", "fix": "<what to change>"}}]}}
Report at most 5 problems, most important first, and only ones that would fail a test the task implies; no
style, no nice-to-haves. Use "ship" with an empty list when you find none."""

EXPLORE_NOTE = """You may first run up to {n} read-only commands in the task container (ls, cat, head, tail, grep,
find, wc, sed -n, git diff) to inspect files or outputs: reply with only {{"explore": "<one command>"}} and you get
its output. """

DIFF_SCRIPT = r"""S={snap}; W={work}
diff -rq -x __pycache__ -x '*.pyc' -x .git -x node_modules "$S" "$W" 2>/dev/null | head -40 | while IFS= read -r line; do
  case "$line" in
    Files*) a=$(printf '%s' "$line" | sed -E 's/^Files (.*) and (.*) differ$/\1/'); b=$(printf '%s' "$line" | sed -E 's/^Files (.*) and (.*) differ$/\2/')
      if [ "$(stat -c %s "$b" 2>/dev/null || echo 9999999)" -lt 80000 ] && grep -Iq . "$b"; then diff -u "$a" "$b" | head -400
      else echo "=== changed (large or binary): $b ($(stat -c %s "$b" 2>/dev/null) bytes)"; fi;;
    "Only in $W"*) p=$(printf '%s' "$line" | sed -E 's/^Only in (.*): (.*)$/\1\/\2/')
      if [ -f "$p" ] && [ "$(stat -c %s "$p")" -lt 80000 ] && grep -Iq . "$p"; then echo "=== new file $p"; head -300 "$p"
      else echo "=== new: $p"; ls -la "$p" 2>/dev/null | head -5; fi;;
    *) echo "$line";;
  esac
done"""


VERIFY_PROMPT = """Your colleague says they fixed the problems you raised. Their report: {report}

WHAT THEY DID SINCE YOUR REVIEW (newest last):
{digest}

WHAT CHANGED NOW (diff against the starting files):
{diff}

For each problem you raised, decide from the code and outputs (not their word) whether it is fixed. {explore}
Reply with only JSON: {{"open": [{{"n": <the problem's number>, "what": "<what is still wrong>", "fix": "<what to change>"}}]}}
Use an empty list when every problem is fixed. Do not raise new problems here."""

FIX_GOAL = """Another engineer did this task. A senior review then found problems, and after their fix these are still
open:
{problems}
Fix exactly these, without undoing parts that work, and check each fix the way the task's own tests would (as a
fresh process, possibly as a non-root user, through the real entry points). Their notes on what they did are
below. Then submit."""


def parse_open(reply: str, problems: list[dict]) -> list[dict]:
    """The problems the reviewer still finds open after the fix ([] when it cannot tell: never block on a broken
    reply)."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    out = []
    for o in (d.get("open") or [])[:5]:
        if not isinstance(o, dict):
            continue
        n = o.get("n")
        base = problems[n - 1] if isinstance(n, int) and 1 <= n <= len(problems) else {}
        what = str(o.get("what") or base.get("what") or "").strip()
        if what:
            out.append({"what": what, "fix": str(o.get("fix") or base.get("fix") or "")})
    return out


def senior_verify(ask, messages: list[dict], problems: list[dict], report: str, digest: str, diff: str, explore=None,
                  max_explore: int = 0):
    """The second look, in the same reviewer conversation (`messages` continues it, so a CLI session only sends
    this turn): which of the raised problems are still open. Returns (open problems, n_explored)."""
    from .planner import _explore_request

    messages = [*messages, {"role": "user", "content": VERIFY_PROMPT.format(
        report=report[:1500] or "(none)", digest=digest or "(none)", diff=diff or "(unchanged)",
        explore=EXPLORE_NOTE.format(n=max_explore) if explore and max_explore else "")}]
    n = 0
    for _ in range(max_explore + 2):
        reply = ask(messages)
        cmd = _explore_request(reply)
        if cmd is None:
            return parse_open(reply, problems), n
        messages.append({"role": "assistant", "content": reply})
        if explore and n < max_explore:
            n += 1
            messages.append({"role": "user", "content": f"$ {cmd}\n{explore(cmd)}"})
        else:
            messages.append({"role": "user", "content": "No more commands: reply with the JSON now."})
    return [], n


EVIDENCE_RULE = """
Back every problem with evidence: a measured number, a command and its output, or the exact line of code. Where
the colleague reported measurements, check that they measured the way the task's tests would (from a fresh
process, the real entry point, the failure cases the task names); an unmeasured or wrongly measured target is a
problem. Do not ask for a change that trades one stated target for another (for example stricter matching for
precision) unless your evidence shows the other target still holds after it. A concern you cannot back with
evidence is not a problem; leave it out."""


def parse_review(reply: str) -> tuple[str, list[dict]]:
    """("ship" | "fix", problems). Anything unparsable ships: a broken review must not block the work."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    probs = [p for p in (d.get("problems") or []) if isinstance(p, dict) and str(p.get("what", "")).strip()][:5]
    verdict = "fix" if d.get("verdict") == "fix" and probs else "ship"
    return verdict, probs if verdict == "fix" else []


def problems_text(probs: list[dict], reviewer: str) -> str:
    lines = [f"a senior reviewer ({reviewer}) checked your work against the task and found:"]
    for i, p in enumerate(probs, 1):
        lines.append(f"{i}. {str(p.get('what'))[:400]}"
                     + (f"\n   evidence: {str(p.get('evidence'))[:300]}" if p.get("evidence") else "")
                     + (f"\n   fix: {str(p.get('fix'))[:300]}" if p.get("fix") else ""))
    lines.append("Fix each one, testing it the way the task's tests would. If you are sure a point is wrong, say "
                 "why in your submit message instead of changing working code.")
    return "\n".join(lines)


def senior_review(ask, task: str, report: str, digest: str, diff: str, explore=None, max_explore: int = 0,
                  transcript: list | None = None, evidence: bool = False):
    """`ask(messages) -> reply` is the reviewer model; `explore(command) -> output` runs a read-only command.
    Returns (verdict, problems, n_explored); `transcript`, when given, receives the review conversation so the
    second look (senior_verify) can continue it."""
    from .planner import _explore_request

    prompt = SENIOR_PROMPT.format(task=task[:6000], report=report[:1500] or "(none)", digest=digest or "(none)",
                                  diff=diff or "(no changes found, or no starting copy to compare with)",
                                  explore=EXPLORE_NOTE.format(n=max_explore) if explore and max_explore else "")
    if evidence:  # v2.5: the rule goes before the reply format, after the checklist
        prompt = prompt.replace("\nThen reply with only JSON", EVIDENCE_RULE + "\nThen reply with only JSON", 1)
    messages, n = [{"role": "user", "content": prompt}], 0
    for _ in range(max_explore + 2):
        reply = ask(messages)
        cmd = _explore_request(reply)
        if cmd is None:
            verdict, probs = parse_review(reply)
            if transcript is not None:
                transcript[:] = [*messages, {"role": "assistant", "content": reply}]
            return verdict, probs, n
        if explore and n < max_explore:
            n += 1
            messages += [{"role": "assistant", "content": reply}, {"role": "user", "content": f"$ {cmd}\n{explore(cmd)}"}]
        else:
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": "No more commands: reply with the review JSON now."}]
    return "ship", [], n
