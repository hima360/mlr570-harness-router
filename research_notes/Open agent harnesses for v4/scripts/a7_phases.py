"""Phase durations per takeover-era run (builder / judge / takeover) and spend on never-solved tasks."""
import json,collections,statistics as st
rows=[r for r in json.load(open("runs.json")) if r["exc"]!="CancelledError"]
P=collections.defaultdict(lambda:collections.defaultdict(list))
for r in rows:
    if not r["calls"]: continue
    b_end=max([c["t"] for c in r["calls"] if c["role"]=="worker"] or [0])
    judge=sum((c["ms"] or 0) for c in r["calls"] if c["role"]=="reviewer")/1000
    ts=[x for x in r["takeovers"] if x["phase"]=="start"]; te=[x for x in r["takeovers"] if x["phase"]=="end"]
    tk=(te[0]["t"]-ts[0]["t"]) if ts and te else 0
    s=r["setup"]
    P[s]["builder_min"].append(b_end/60); P[s]["judge_min"].append(judge/60); P[s]["takeover_min"].append(tk/60)
    P[s]["agent_min"].append((r["t_agent"] or 0)/60); P[s]["verifier_min"].append((r["t_verifier"] or 0)/60)
    P[s]["glm_calls"].append(sum(1 for c in r["calls"] if c["role"]=="worker"))
print(f"{'setup':12} {'n':>3} " + " ".join(f"{k:>14}" for k in ["builder_min","judge_min","takeover_min","agent_min","verifier_min","glm_calls"])+"   (sum over runs, minutes)")
for s,d in P.items():
    print(f"{s:12} {len(d['agent_min']):3} " + " ".join(f"{sum(d[k]):14.0f}" for k in ["builder_min","judge_min","takeover_min","agent_min","verifier_min","glm_calls"]))
never={"cargo-flight-dispatch","heat-pump-warranty","kv-live-surgery","session-window-debug","vf2-speedup-networkx"}
dev=[r for r in rows if r["prefix"] not in ("dv6","dv7")]
tot=sum(r["usd"] for r in dev); nv=sum(r["usd"] for r in dev if r["task"] in never)
print(f"\nspend on 5 dev tasks no setup ever solved: ${nv:.2f} of ${tot:.2f} ({nv/tot:.0%}) over {sum(r['task'] in never for r in dev)} runs")
for t in sorted(never):
    rs=[r for r in dev if r["task"]==t]
    fr=collections.Counter(round(r["tests"] or 0,2) for r in rs)
    print(f"  {t:24} runs={len(rs)} ${sum(r['usd'] for r in rs):.2f} tests-share distribution={dict(fr)}")
# Opus spend in takeover-era runs on those
tk=[r for r in dev if r["setup"].startswith("takeover")]
print(f"takeover-era runs: ${sum(r['usd'] for r in tk):.2f}; on never-solved tasks ${sum(r['usd'] for r in tk if r['task'] in never):.2f}")
vs=sorted(((r["t_verifier"] or 0),r["name"]) for r in rows)[-6:]; print("slowest verifiers:",[(round(a),b) for a,b in vs])
