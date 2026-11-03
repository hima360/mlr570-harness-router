"""Routing labels from our own runs: which strength of model a step (or a whole task) needed.

  python -m phase2.labels --out labels_steps.json jobs .old-harness/jobs .cap-harness/jobs

Model strength classes are model-agnostic: 0 = small (Haiku, Luna, GLM Flash), 1 = mid (Sonnet, GLM-5.3),
2 = frontier (Opus, GPT-6.1 Sol). Labels, per step of every trace:
  - passed on its first attempt by a class-c model: label c, censored (a cheaper model might also have done it),
    except c = 0, which is exact;
  - failed on class c, then passed on c' > c: label c' (exact);
  - never passed: label 2, censored (no model we tried did it).
Whole-task labels come from single-step runs judged by the task's own tests (reward), not by our checks.
Step labels are judged by the planner's step checks, which are weaker (2026-10-27: 15 of 20 failed runs had a
passing check), so they are noisy.
"""
import argparse
import glob
import json
import os
from pathlib import Path

SMALL = ("haiku", "luna", "flash")
FRONTIER = ("opus", "sol")


def strength(model: str) -> int:
    m = model.lower()
    if any(k in m for k in SMALL):
        return 0
    if any(k in m for k in FRONTIER):
        return 2
    return 1


def step_labels(trace_path: str) -> list[dict]:
    events = [json.loads(line) for line in open(trace_path) if line.strip()]
    plan = next((e for e in events if e.get("event") == "plan"), None)
    if not plan:
        return []
    goals = {s["id"]: s["goal"] for s in plan["steps"]}
    merged = next((e for e in events if e.get("event") == "merged"), None)
    if merged:  # the merged step's goal is the checklist the router saw
        goals = {merged["into"]: "\n".join(goals[p] for p in merged["parts"] if p in goals)}
    out = []
    for sid, goal in goals.items():
        atts = [e for e in events if e.get("event") == "attempt" and e.get("step") == sid]
        if not atts:
            continue
        classes = [strength(a["model"]) for a in atts]
        passed = [a for a in atts if a.get("passed")]
        if passed:
            c = strength(passed[0]["model"])
            exact = c == 0 or any(strength(a["model"]) < c and not a.get("passed") for a in atts)
            out.append({"text": goal, "label": c, "censored": not exact, "source": "step_check"})
        else:
            out.append({"text": goal, "label": 2, "censored": True, "source": "step_check",
                        "tried": sorted(set(classes))})
    return out


def task_label(trial_dir: str) -> dict | None:
    """A single-step run judged by the task's own tests: (task, strength class, solved)."""
    tr = os.path.join(trial_dir, "agent", "trace.jsonl")
    res = os.path.join(trial_dir, "result.json")
    if not (os.path.exists(tr) and os.path.exists(res)):
        return None
    events = [json.loads(line) for line in open(tr) if line.strip()]
    plan = next((e for e in events if e.get("event") == "plan"), None)
    atts = [e for e in events if e.get("event") == "attempt"]
    if not plan or len(plan["steps"]) != 1 or not atts:
        return None
    r = json.load(open(res))
    reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    if reward is None:
        return None
    return {"task": r.get("task_name") or Path(trial_dir).name.split("__")[0], "text": plan["steps"][0]["goal"],
            "class": strength(atts[0]["model"]), "solved": reward == 1.0}


def collect(roots: list[str]) -> dict:
    steps, tasks = [], {}
    for root in roots:
        for tr in glob.glob(os.path.join(root, "*", "*", "agent", "trace.jsonl")):
            steps += step_labels(tr)
            t = task_label(os.path.dirname(os.path.dirname(tr)))
            if t:
                tasks.setdefault(t["task"], {"text": t["text"], "runs": []})["runs"].append((t["class"], t["solved"]))
    task_rows = []
    for name, d in tasks.items():
        solved = sorted(c for c, ok in d["runs"] if ok)
        tried = sorted({c for c, _ in d["runs"]})
        if solved:
            lab = solved[0]
            exact = lab == 0 or any(c < lab for c in tried)
        else:
            lab, exact = 2, False
        task_rows.append({"task": name, "text": d["text"], "label": lab, "censored": not exact, "source": "task_reward"})
    return {"steps": steps, "tasks": task_rows}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+", help="jobs directories to scan")
    ap.add_argument("--out", default="labels_steps.json")
    a = ap.parse_args(argv)
    data = collect(a.roots)
    Path(a.out).write_text(json.dumps(data, indent=2))
    for kind in ("steps", "tasks"):
        rows = data[kind]
        counts = [sum(r["label"] == c for r in rows) for c in range(3)]
        print(f"{kind}: {len(rows)} labelled ({sum(not r['censored'] for r in rows)} exact), per class {counts}")


if __name__ == "__main__":
    main()
