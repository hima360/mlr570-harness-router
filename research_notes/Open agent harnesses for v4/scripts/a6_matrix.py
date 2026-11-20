"""Task x setup matrix on the 12 dev tasks: S=solved, tests share, $; X=stopped; E=infra."""
import json,collections
rows=json.load(open("runs.json"))
S=["glm","opus","glmreview27","takeover28","takeover29","takeover30","takeover31","takeover35","takeover37"]
DEV12="cargo-flight-dispatch embedding-drift-monitor fin-saccr-rwa heat-pump-warranty html-js-filter kv-live-surgery mvcc-lsm-compaction payments-pipeline-fix react-lead-form risk-scorer-replay session-window-debug vf2-speedup-networkx".split()
m={}
for r in rows:
    if r["prefix"]=="dv7" or r["prefix"]=="dv6": continue
    m[(r["task"],r["setup"])]=r
print(f"{'task':24}"+"".join(f"{s[:11]:>13}" for s in S))
tot=collections.defaultdict(lambda:[0,0,0.0])
for t in DEV12:
    line=f"{t[:24]:24}"
    for s in S:
        r=m.get((t,s))
        if not r: line+=f"{'-':>13}"; continue
        if r["exc"]=="CancelledError": cell="X"
        else:
            cell=("S" if r["reward"]==1 else f"{(r['tests'] or 0):.2f}")+f" ${r['usd']:.2f}"
            tot[s][0]+=1; tot[s][1]+=r["reward"]==1; tot[s][2]+=r["usd"]
        line+=f"{cell:>13}"
    print(line)
print(f"{'valid runs/solves/$':24}"+"".join(f"{f'{v[0]}/{v[1]}/{v[2]:.2f}':>13}" for v in (tot[s] for s in S)))
# solved by whom
who=collections.defaultdict(collections.Counter)
for r in rows:
    if r["reward"]!=1: continue
    who[r["setup"]]["takeover" if any(x["phase"]=="start" for x in r["takeovers"]) else ("builder_fix" if r["builder_fix"] else "builder/single")]+=1
print({k:dict(v) for k,v in who.items()})
st=[r for r in rows if r["exc"]=="CancelledError"]
print("stopped runs:",len(st),"$",round(sum(r["usd"] for r in st),2))
