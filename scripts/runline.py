"""One line per finished run: task, reward, list-price cost, check results, judge verdicts, takeovers.
  python scripts/runline.py jobs/<job>/<trial>/result.json"""
import json, sys
from pathlib import Path

P = {"opus": (4.0, 0.20, 20.0), "glm": (0.15, 0.03, 0.50)}
f = Path(sys.argv[1])
r = json.loads(f.read_text())
reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
cost, checks, verdicts, tk = 0.0, [], [], 0
for line in (f.parent / "agent" / "trace.jsonl").read_text().splitlines():
    e = json.loads(line)
    ev = e.get("event")
    if ev == "checks_run":
        checks.append((e.get("found"), e.get("passes"), e.get("fails")))
    elif ev == "senior_review":
        verdicts.append(e.get("verdict"))
    elif ev == "takeover" and e.get("phase") == "start":
        tk += 1
    elif ev == "llm_call":
        m = e.get("model", "")
        fam = "opus" if "opus" in m else "glm" if "glm" in m else None
        if fam:
            u, c, o = P[fam]
            i, ca = e.get("input_tokens") or 0, e.get("cached_tokens") or 0
            cost += ((i - ca) * u + ca * c + (e.get("output_tokens") or 0) * o) / 1e6
name = f.parents[1].name.split("-", 2)[2]
print(f"{name} reward={reward} ${cost:.2f} checks={checks} verdict={verdicts} takeover={tk}")
