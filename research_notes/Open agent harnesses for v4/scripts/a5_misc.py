"""Dead-container commands; 'python: command not found'; per-run wasted calls."""
import json,collections,re
rows=json.load(open("runs.json"))
dead=collections.Counter(); py=collections.Counter(); nf=collections.Counter()
for r in rows:
    for kind,tj in r["trajs"].items():
        for c in tj["cmds"]:
            t=c["obs_tail"]
            if 'service "main" is not running' in t or "is not running" in t and "service" in t: dead[(r["name"],r["exc"])]+=1
            if re.search(r"\bpython: command not found",t): py[r["name"]]+=1
            m=re.search(r"line \d+: (\S+): command not found",t)
            if m: nf[m.group(1)]+=1
print("commands sent to a dead container:",sum(dead.values()),dead.most_common())
print("'python: command not found':",sum(py.values()),"in",len(py),"runs")
print("command-not-found names:",nf.most_common())
