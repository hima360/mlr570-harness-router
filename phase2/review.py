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
transcript does not back up.
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


def senior_review(ask, task: str, report: str, digest: str, diff: str, explore=None, max_explore: int = 0):
    """`ask(messages) -> reply` is the reviewer model; `explore(command) -> output` runs a read-only command.
    Returns (verdict, problems, n_explored)."""
    from .planner import _explore_request

    prompt = SENIOR_PROMPT.format(task=task[:6000], report=report[:1500] or "(none)", digest=digest or "(none)",
                                  diff=diff or "(no changes found, or no starting copy to compare with)",
                                  explore=EXPLORE_NOTE.format(n=max_explore) if explore and max_explore else "")
    messages, n = [{"role": "user", "content": prompt}], 0
    for _ in range(max_explore + 2):
        reply = ask(messages)
        cmd = _explore_request(reply)
        if cmd is None:
            verdict, probs = parse_review(reply)
            return verdict, probs, n
        if explore and n < max_explore:
            n += 1
            messages += [{"role": "assistant", "content": reply}, {"role": "user", "content": f"$ {cmd}\n{explore(cmd)}"}]
        else:
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": "No more commands: reply with the review JSON now."}]
    return "ship", [], n
