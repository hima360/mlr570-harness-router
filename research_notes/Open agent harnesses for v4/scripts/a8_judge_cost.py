"""Judge (reviewer) cost structure: calls per review, output tokens per call, $ per run."""
import json,collections,statistics as st
rows=[r for r in json.load(open("runs.json")) if r["exc"]!="CancelledError"]
per=[];outs=[];usd=[];ratio=[]
for r in rows:
    rc=[c for c in r["calls"] if c["role"]=="reviewer"]
    if not rc: continue
    per.append(len(rc)); usd.append(r["usd_role"].get("reviewer",0)); ratio.append(r["usd_role"].get("reviewer",0)/r["usd"])
    outs+= [c["output_tokens"] or 0 for c in rc]
outs.sort()
print(f"runs with judge {len(per)}; calls/run median {st.median(per)} max {max(per)}; judge $ total {sum(usd):.2f}, median/run {st.median(usd):.2f}; median share of run $ {st.median(ratio):.0%}")
print(f"judge output tokens/call median {st.median(outs)} p90 {outs[int(.9*len(outs))]} ; reply text <=300 chars in trace, so most output is hidden reasoning (inferred)")
tk=[c for r in rows for c in r["calls"] if c["role"]=="takeover" and "opus" in c["model"]]
o=sorted(c["output_tokens"] or 0 for c in tk); print(f"Opus takeover output tokens/call median {st.median(o)} p90 {o[int(.9*len(o))]}")
tot=collections.Counter()
for r in rows:
    for m,v in r["usd_model"].items(): tot[m]+=v
print("total $ by model (valid dev runs):",{k:round(v,2) for k,v in tot.items()})
role=collections.Counter()
for r in rows:
    if r["setup"].startswith("takeover"):
        for k,v in r["usd_role"].items(): role[k]+=v
print("takeover-era $ by role:",{k:round(v,2) for k,v in role.items()})
