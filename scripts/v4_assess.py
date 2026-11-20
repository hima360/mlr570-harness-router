"""v4 dev assessment: v4d6 (ocxv4, 2 repeats) vs dv8 (v2.8) and dv13 (v3.5) per task, list-price cost from tokens.
Runs the supervisor marks as infrastructure-ended are excluded. Run from the repo root: python scripts/v4_assess.py"""
import json,glob,sys,collections
sys.path.insert(0,"scripts"); import supervise
from pathlib import Path
P={"opus":(4.0,0.20,20.0),"glm":(0.15,0.03,0.50)}
def run(d):
  f=glob.glob(d+"/*/result.json")
  if not f: return None
  r=json.load(open(f[0])); rw=((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
  cost=opus=0; tk=0; chk=None; verdict=None; bexit=None
  for l in open(f[0].rsplit("/",1)[0]+"/agent/trace.jsonl"):
    e=json.loads(l); ev=e.get("event")
    if ev=="takeover" and e.get("phase")=="start": tk=1
    if ev=="checks_run": chk=(e.get("found"),e.get("passes"),e.get("fails"),e.get("exit"))
    if ev=="senior_review" and verdict is None: verdict=e.get("verdict"); bexit=e.get("builder_exit")
    if ev!="llm_call": continue
    m=e.get("model",""); fm="opus" if "opus" in m else "glm" if "glm" in m else None
    if not fm: continue
    u,c,o=P[fm]; i=e.get("input_tokens") or 0; ca=e.get("cached_tokens") or 0
    x=((i-ca)*u+ca*c+(e.get("output_tokens") or 0)*o)/1e6; cost+=x; opus+=x if fm=="opus" else 0
  return dict(reward=rw,cost=cost,opus=opus,tk=tk,chk=chk,verdict=verdict,bexit=bexit,infra=supervise.infra_reason(Path(d)))
tasks=["cargo-flight-dispatch","embedding-drift-monitor","fin-saccr-rwa","heat-pump-warranty","html-js-filter","kv-live-surgery","mvcc-lsm-compaction","payments-pipeline-fix","react-lead-form","risk-scorer-replay","session-window-debug","vf2-speedup-networkx"]
rows=[]
for t in tasks:
  a=run(f"jobs/dv8-takeover28-{t}"); b=run(f"jobs/dv13-takeover35-{t}")
  v=[run(f"jobs/v4d6-ocxv4-{t}-r{i}") for i in (1,2)]
  rows.append((t,a,b,v))
  vs=" ".join("-" if x is None else ("INFRA" if x["infra"] else f"{int(x['reward'] or 0)} ${x['cost']:.2f} tk{x['tk']} {x['verdict']} chk{x['chk'][1] if x['chk'] else '-'}") for x in v)
  print(f"{t:25s} v2.8 {int(a['reward'] or 0)} ${a['cost']:.2f} | v3.5 {int(b['reward'] or 0)} ${b['cost']:.2f} | v4 {vs}")
# per-run averages, valid v4 runs only
v4=[x for _,_,_,v in rows for x in v if x and not x["infra"]]
n=len(v4); s=sum(x["reward"]==1 for x in v4); c=sum(x["cost"] for x in v4); k=sum(x["tk"] for x in v4); o=sum(x["opus"] for x in v4)
print(f"\nv4 valid runs {n}: solve rate {s}/{n}={s/n:.2f}, cost/run ${c/n:.2f}, takeover rate {k/n:.2f}, opus share {o/c:.0%}, cost/solve ${c/max(s,1):.2f}")
for name,idx in (("v2.8 dv8",1),("v3.5 dv13",2)):
  xs=[r[idx] for r in rows]; s=sum(x["reward"]==1 for x in xs); c=sum(x["cost"] for x in xs); k=sum(x["tk"] for x in xs)
  print(f"{name} runs 12: solve rate {s}/12={s/12:.2f}, cost/run ${c/12:.2f}, takeover rate {k/12:.2f}, cost/solve ${c/max(s,1):.2f}")
g=[x for x in v4 if x["chk"] and x["chk"][0] and x["chk"][2]==0 and x["chk"][1]>0]
print(f"green checks {len(g)}: judge ship {sum(x['verdict']=='ship' for x in g)}, fix {sum(x['verdict']=='fix' for x in g)}; of fixed, solved {sum(x['reward']==1 for x in g if x['verdict']=='fix')}; shipped solved {sum(x['reward']==1 for x in g if x['verdict']=='ship')}")
print("builder exits", collections.Counter(x["bexit"] for x in v4))
