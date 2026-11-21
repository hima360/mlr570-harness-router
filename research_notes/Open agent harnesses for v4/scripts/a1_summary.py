"""Per prefix/setup: runs, solves, cost, wall time; time split; cross-check of cost sources."""
import json, os, statistics as st, collections
H = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(f"{H}/runs.json"))
VER = {"glm": "GLM alone", "opus": "Opus alone", "glmreview26": "v2.6", "glmreview27": "v2.7", "takeover28": "v2.8",
       "takeover29": "v2.9", "takeover30": "v3.0", "takeover31": "v3.1", "takeover34": "v3.4", "takeover35": "v3.5",
       "takeover36": "v3.6", "takeover37": "v3.7"}
g = collections.defaultdict(list)
for r in rows:
    g[(r["prefix"], r["setup"])].append(r)
print(f"{'prefix':6} {'setup':12} {'ver':10} runs solv  usd   usd/run usd/solve repo$  mu$   med_min  tot_h  env_s  agent_min verif_s exc")
tot = collections.Counter()
for (p, s), rs in sorted(g.items(), key=lambda kv: (kv[0][0] != "dt1", int(kv[0][0][2:]) if kv[0][0] != "dt1" else 0, kv[0][1])):
    n = len(rs); sol = sum(r["reward"] == 1 for r in rs); u = sum(r["usd"] for r in rs)
    excs = collections.Counter(r["exc"] for r in rs if r["exc"])
    print(f"{p:6} {s:12} {VER.get(s,s):10} {n:4} {sol:4} {u:6.2f} {u/n:7.3f} {(u/sol if sol else float('nan')):8.2f} "
          f"{sum(r['usd_repo'] for r in rs):6.2f} {sum(r['usd_mu'] for r in rs):6.2f} "
          f"{st.median(r['t_total'] for r in rs)/60:7.1f} {sum(r['t_total'] for r in rs)/3600:6.2f} "
          f"{st.median(r['t_env'] or 0 for r in rs):6.0f} {st.median(r['t_agent'] or 0 for r in rs)/60:8.1f} "
          f"{st.median(r['t_verifier'] or 0 for r in rs):7.0f} {dict(excs)}")
    tot["runs"] += n; tot["solves"] += sol; tot["usd"] += u
print(dict(tot))

# time split over all runs
T = collections.Counter()
for r in rows:
    T["total"] += r["t_total"]; T["env"] += r["t_env"] or 0; T["agent_setup"] += r["t_agent_setup"] or 0
    T["agent"] += r["t_agent"] or 0; T["verifier"] += r["t_verifier"] or 0
    for role, ms in r["llm_ms"].items():
        T["llm_" + role] += ms / 1000
T["other(harbor: artifacts copy, teardown)"] = T["total"] - T["env"] - T["agent_setup"] - T["agent"] - T["verifier"]
llm = sum(v for k, v in T.items() if k.startswith("llm_"))
T["agent_non_llm(commands+overhead)"] = T["agent"] - llm
print("\nTime split, all runs (hours, % of total):")
for k, v in T.items():
    print(f"  {k:42} {v/3600:7.2f} h  {100*v/T['total']:5.1f}%")
# verifier time distribution
vs = sorted(r["t_verifier"] or 0 for r in rows)
print("verifier sec: median %.0f p90 %.0f max %.0f" % (st.median(vs), vs[int(.9*len(vs))], vs[-1]))
es = sorted(r["t_env"] or 0 for r in rows)
print("env setup sec: median %.0f p90 %.0f max %.0f" % (st.median(es), es[int(.9*len(es))], es[-1]))
# cost cross-check
diff = [(r["name"], r["usd"], r["usd_mu"]) for r in rows if abs(r["usd"] - r["usd_mu"]) > 0.05]
print(f"\nruns where trace cost and result.json model_usage cost differ by > $0.05: {len(diff)}")
for x in diff[:15]:
    print("  ", x)
