"""Judge verdicts and ship accuracy; takeovers; Opus prompt cache hits and breaks; LLM latency."""
import json, os, statistics as st, collections
H = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(f"{H}/runs.json"))
STOPPED = {"CancelledError"}
valid = [r for r in rows if r["exc"] not in STOPPED]
print(f"runs {len(rows)}, excluding operator-stopped (CancelledError) {len(rows)-len(valid)} -> {len(valid)}")

# ---------- judge ----------
print("\n== Judge (senior_review) ==")
per = collections.defaultdict(collections.Counter)
ship_runs, fix_runs = [], []
for r in valid:
    rv = [x for x in r["reviews"] if x.get("verdict")]
    errs = [x for x in r["reviews"] if x.get("error")]
    if errs:
        per[r["setup"]]["review_error"] += 1
    if not rv:
        continue
    final = rv[-1]["verdict"]
    per[r["setup"]]["reviewed"] += 1
    per[r["setup"]][final] += 1
    per[r["setup"]]["rounds"] += len(rv)
    (ship_runs if final == "ship" else fix_runs).append(r)
    if final == "ship":
        per[r["setup"]]["ship_correct"] += r["reward"] == 1
    else:
        per[r["setup"]]["fix_solved"] += r["reward"] == 1
for s, c in per.items():
    print(f"  {s:12} {dict(c)}")
print(f"  ALL: reviewed runs {len(ship_runs)+len(fix_runs)}; final ship {len(ship_runs)} (verifier pass {sum(r['reward']==1 for r in ship_runs)}); "
      f"final fix {len(fix_runs)} (eventually solved {sum(r['reward']==1 for r in fix_runs)})")
print("  ship runs:", [(r['name'], r['reward'], round(r['tests'] or 0, 2)) for r in ship_runs])
# first-round verdict vs builder exit
fx = collections.Counter()
for r in valid:
    rv = [x for x in r["reviews"] if x.get("verdict")]
    if rv:
        fx[(rv[0].get("builder_exit") or "?", rv[0]["verdict"])] += 1
print("  first verdict by builder exit:", dict(fx))
# would-GLM-alone-have-passed proxy: builder attempt passed + GLM alone (dt1-glm) solved that task
glm_solved = {r["task"] for r in rows if r["setup"] == "glm" and r["reward"] == 1}
print("  tasks GLM alone solved in dt1:", sorted(glm_solved))
fx_on_glm = [(r["name"], r["reward"]) for r in fix_runs if r["task"] in glm_solved]
print("  'fix' verdicts on tasks GLM alone solved in dt1 (inferred: possible needless takeovers):", fx_on_glm)

# ---------- takeovers ----------
print("\n== Takeovers ==")
T = []
for r in valid:
    starts = [x for x in r["takeovers"] if x["phase"] == "start"]
    ends = [x for x in r["takeovers"] if x["phase"] == "end"]
    if not starts:
        continue
    tcalls = [c for c in r["calls"] if c["role"] == "takeover"]
    T.append(dict(name=r["name"], setup=r["setup"], model=starts[0]["model"].split("/")[-1], solved=r["reward"] == 1,
                  tests=r["tests"], n_calls=len(tcalls), exit=(ends[0]["exit_status"] if ends else "none"),
                  usd=r["usd_role"].get("takeover", 0), usd_run=r["usd"],
                  minutes=(ends[0]["t"] - starts[0]["t"]) / 60 if ends else None))
by = collections.defaultdict(list)
for x in T:
    by[x["setup"]].append(x)
for s, xs in by.items():
    print(f"  {s:12} n={len(xs)} solved={sum(x['solved'] for x in xs)} calls median={st.median(x['n_calls'] for x in xs)} "
          f"takeover$={sum(x['usd'] for x in xs):.2f} run$={sum(x['usd_run'] for x in xs):.2f} "
          f"share={sum(x['usd'] for x in xs)/max(sum(x['usd_run'] for x in xs),1e-9):.0%}")
allx = T
print(f"  ALL n={len(allx)} solved={sum(x['solved'] for x in allx)} takeover$={sum(x['usd'] for x in allx):.2f} "
      f"of run$={sum(x['usd_run'] for x in allx):.2f}")
print("  exits:", dict(collections.Counter(x["exit"] for x in allx)))
sol = [x["n_calls"] for x in allx if x["solved"]]; fail = [x["n_calls"] for x in allx if not x["solved"]]
print(f"  calls: solved median {st.median(sol) if sol else '-'} {sorted(sol)}; failed median {st.median(fail)} {sorted(fail)}")
print(f"  $ per takeover: solved {[round(x['usd'],2) for x in allx if x['solved']]}")
print(f"                  failed {[round(x['usd'],2) for x in allx if not x['solved']]}")
nearmiss_fail = [(x['name'], round(x['tests'] or 0, 2), x['n_calls'], round(x['usd'], 2)) for x in allx if not x['solved']]
print("  failed takeovers (name, tests share, calls, $):")
for y in sorted(nearmiss_fail, key=lambda y: -y[1]):
    print("    ", y)

# ---------- cache ----------
print("\n== Prompt cache, Opus calls ==")
role_c = collections.defaultdict(lambda: collections.Counter())
breaks = []
for r in valid:
    prev_by_role = {}
    for c in r["calls"]:
        if not c["model"].endswith("claude-opus-5-5"):
            continue
        inp, cac = c["input_tokens"] or 0, c["cached_tokens"] or 0
        rc = role_c[c["role"]]
        rc["calls"] += 1; rc["in"] += inp; rc["cached"] += cac
        rc["fresh_usd"] += (inp - cac) * 4 / 1e6; rc["cache_usd"] += cac * .2 / 1e6; rc["out_usd"] += (c["output_tokens"] or 0) * 20 / 1e6
        rc["sess_" + str(c["session"])] += 1
        hit = cac / inp if inp else 0
        first = c["role"] not in prev_by_role
        if c["session"] == "resume" and hit < 0.5:
            kind = "resume_low_hit"
        elif c["session"] == "new" and not first:
            kind = "new_session_restart"
        else:
            kind = None
        if kind:
            breaks.append(dict(run=r["name"], role=c["role"], kind=kind, inp=inp, cached=cac,
                               fresh_usd=(inp - cac) * 4 / 1e6, setup=r["setup"], sbc=r["session_budget_chars"],
                               gap=c["t"] - prev_by_role[c["role"]] if not first else None))
        prev_by_role[c["role"]] = c["t"]
for role, rc in role_c.items():
    print(f"  {role:10} calls={rc['calls']} hit={rc['cached']/max(rc['in'],1):.1%} fresh$={rc['fresh_usd']:.2f} "
          f"cache$={rc['cache_usd']:.2f} out$={rc['out_usd']:.2f} sessions new/resume/None={rc['sess_new']}/{rc['sess_resume']}/{rc['sess_None']}")
print(f"  breaks: {len(breaks)} cost fresh ${sum(b['fresh_usd'] for b in breaks):.2f}")
bk = collections.defaultdict(lambda: [0, 0.0])
for b in breaks:
    k = (b["setup"], b["role"], b["kind"]); bk[k][0] += 1; bk[k][1] += b["fresh_usd"]
for k, v in sorted(bk.items()):
    print(f"    {k} n={v[0]} ${v[1]:.2f}")
gaps = [b["gap"] for b in breaks if b["kind"] == "resume_low_hit" and b["gap"] is not None]
if gaps:
    print(f"  resume_low_hit: time since previous call of same role, sec: median {st.median(gaps):.0f}, >300s: {sum(g>300 for g in gaps)}/{len(gaps)}")
# first-call (unavoidable) fresh input
# ---------- latency ----------
print("\n== LLM latency by model/role (sec per call) ==")
lat = collections.defaultdict(list)
for r in valid:
    for c in r["calls"]:
        lat[(c["model"].split("/")[-1], c["role"])].append((c["ms"] or 0) / 1000)
for k, v in sorted(lat.items()):
    v = sorted(v)
    print(f"  {k}: n={len(v)} median={st.median(v):.1f}s p90={v[int(.9*len(v))]:.1f}s total={sum(v)/3600:.2f}h")
# output tokens per GLM worker call
o = sorted(c["output_tokens"] or 0 for r in valid for c in r["calls"] if c["role"] == "worker" and "flash" in c["model"])
print(f"  GLM-flash worker output tokens per call: median {st.median(o)} p90 {o[int(.9*len(o))]}")
fails = collections.Counter((f["role"], f["model"].split('/')[-1], f["kind"]) for r in valid for f in r["call_failed"])
print("  llm_call_failed:", dict(fails))
