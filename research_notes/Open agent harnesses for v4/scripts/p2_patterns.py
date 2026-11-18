"""Plan/revise study, step 2: plan shape (Q1), revise/fix/takeover outcomes and cost (Q2), solve paths (Q3a).
Reads study.json (p1_build.py). Valid runs = not operator-stopped (CancelledError) and with a trace.
Counterfactual proxy for "would the builder alone have passed": the dt1 GLM-alone run of the same task (n=1 per task)."""
import collections, glob, json, os, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/Users/hima/Developer/mlr570-harness-router/jobs/"
S = json.load(open(f"{HERE}/study.json"))
V = [o for o in S if not o["stopped"] and o["has_trace"]]
glm1 = {o["task"]: o["solved"] for o in V if o["prefix"] == "dt1" and o["setup"] == "glm"}
opus1 = {o["task"]: (o["solved"], o["usd"]) for o in V if o["prefix"] == "dt1" and o["setup"] == "opus"}


def f(x):
    return f"{x:.2f}"


print("=== Q1 plan shape (all 127 traced dev runs, stopped included)")
c = collections.Counter((o["setup"], tuple(o["plan_steps"]), o["max_plan_steps"], o["planner_calls"])
                        for o in S if o["has_trace"])
for k, v in sorted(c.items()):
    print(f"  setup={k[0]:12s} plan_steps={k[1]} max_plan_steps={k[2]} planner_llm_calls={k[3]}: {v} runs")
print("  traced runs with >1 plan step:", sum(1 for o in S if any(n > 1 for n in o["plan_steps"])))

print("\n=== Q1b plan-before-work analogues")
for o in V:
    if o["strategy"]:
        print(f"  strategist {o['name']:48s} mode={o['strategy']['mode']:10s} deliverables={o['strategy']['n_deliverables']}"
              f" solved={o['solved']} tests={o['tests']} strategist$={f(o['usd_phase']['strategist'])} run$={f(o['usd'])}")
for o in V:
    if o["usd_phase"]["spec"]:
        print(f"  spec card  {o['name']:48s} verdicts={o['verdicts']} solved={o['solved']} spec$={f(o['usd_phase']['spec'])}"
              f" run$={f(o['usd'])} handoff_note_chars={o['handoff_note']}")

# --- per-task paired comparisons
def table(sa, sb, pa=None, pb=None):
    A = {o["task"]: o for o in V if o["setup"] == sa and (pa is None or o["prefix"] == pa)}
    B = {o["task"]: o for o in V if o["setup"] == sb and (pb is None or o["prefix"] == pb)}
    common = sorted(set(A) & set(B))
    sa_ = sum(A[t]["solved"] for t in common); sb_ = sum(B[t]["solved"] for t in common)
    ca = sum(A[t]["usd"] for t in common); cb = sum(B[t]["usd"] for t in common)
    print(f"  {sa}({pa}) vs {sb}({pb}) on {len(common)} shared tasks: {sa_} vs {sb_} solves, ${f(ca)} vs ${f(cb)}")
    for t in common:
        if A[t]["solved"] != B[t]["solved"]:
            print(f"     differs: {t}: {sa}={A[t]['solved']} ({A[t]['tests']}) {sb}={B[t]['solved']} ({B[t]['tests']})")


print("\n=== Q1c paired: strategist/spec vs without")
table("glmreview27", "glmreview26", "dv7", "dv6")
table("glmreview27", "glm", "dt1", "dt1")
table("takeover31", "takeover28")
table("takeover31", "takeover35")

print("\n=== Q2a in-loop revise (glmreview26/27): Opus reviews GLM's submit (or 85% checkpoint), problems go back")
print("    into the same GLM session")
rev = [o for o in V if o["setup"].startswith("glmreview")]
by = collections.defaultdict(list)
for o in rev:
    v = o["verdicts"][0] if o["verdicts"] else "none"
    by[v].append(o)
for v, L in by.items():
    print(f"  verdict={v}: {len(L)} runs, solved {sum(o['solved'] for o in L)}; total ${f(sum(o['usd'] for o in L))};"
          f" review ${f(sum(o['usd_phase']['review'] for o in L))}; revise (GLM after review) ${f(sum(o['usd_phase']['revise'] for o in L))};"
          f" revise calls median {st.median([o['calls_phase']['revise'] for o in L]) if L else 0}")
print("  fix-verdict runs, per task, against the dt1 GLM-alone run of the same task:")
cells = collections.Counter()
for o in by.get("fix", []):
    g = glm1.get(o["task"])
    cells[(g, o["solved"])] += 1
    # checkpoint-triggered review? (worker log carries the harness message)
    wp = glob.glob(ROOT + o["name"] + "/*/agent/worker_s1_a1.json")
    ck = bool(wp) and "before your budget runs out" in open(wp[0]).read()
    print(f"     {o['name']:46s} solved={o['solved']!s:5} tests={o['tests']} glm_alone_solved={g} checkpoint={ck}"
          f" review+revise ${f(o['usd_phase']['review'] + o['usd_phase']['revise'])} run ${f(o['usd'])}")
print("  (glm_alone_solved, revise_run_solved) counts:", dict(cells))

print("\n=== Q2b builder-fix-first (v2.9 takeover29)")
for o in V:
    if o["setup"] == "takeover29":
        print(f"  {o['name']:44s} verdicts={o['verdicts']} builder_fix={o['builder_fix']} fix_exit={o['builder_fix_exit']}"
              f" fix_calls={o['builder_fix_calls']} fix$={f(o['usd_phase']['builder_fix'])} takeover={o['takeover']}"
              f" solved={o['solved']} tests={o['tests']} run$={f(o['usd'])} glm_alone={glm1.get(o['task'])}")

print("\n=== Q2c judge 'fix' -> takeover (v2.8..v3.7)")
tk = [o for o in V if o["setup"].startswith("takeover")]
grp = collections.defaultdict(list)
for o in tk:
    path = "takeover" if o["takeover"] else ("ship" if o["verdicts"] and o["verdicts"][-1] == "ship" else "no-judge")
    grp[(o["takeover_model"] or "-").split("/")[-1], path].append(o)
for k, L in sorted(grp.items()):
    print(f"  {k}: {len(L)} runs, solved {sum(o['solved'] for o in L)}, total ${f(sum(o['usd'] for o in L))},"
          f" takeover ${f(sum(o['usd_phase']['takeover'] for o in L))}, judge ${f(sum(o['usd_phase']['review'] for o in L))},"
          f" builder ${f(sum(o['usd_phase']['builder'] + o['usd_phase']['builder_fix'] for o in L))}")
cells = collections.Counter(); cellcost = collections.defaultdict(float)
for o in tk:
    if not o["takeover"]:
        continue
    g = glm1.get(o["task"])
    cells[(g, o["solved"])] += 1; cellcost[(g, o["solved"])] += o["usd_phase"]["takeover"] + o["usd_phase"]["review"]
print("  takeover runs by (dt1 GLM alone solved task?, takeover run solved?): count, judge+takeover $")
for k in sorted(cells, key=str):
    print(f"     {k}: {cells[k]} runs, ${f(cellcost[k])}")
print("  ship runs (no takeover): solved / n by GLM-alone outcome:")
cells = collections.Counter()
for o in tk:
    if not o["takeover"] and o["verdicts"]:
        cells[(glm1.get(o["task"]), o["solved"])] += 1
print("    ", dict(cells))

print("\n=== Q2d per task: takeover-family (Opus takeover only: 28,29,31,34,35,36) vs GLM alone vs Opus alone")
opus_tk = [o for o in tk if o["setup"] in ("takeover28", "takeover29", "takeover31", "takeover34", "takeover35", "takeover36")]
T = collections.defaultdict(list)
for o in opus_tk:
    T[o["task"]].append(o)
for t, L in sorted(T.items()):
    tko = [o for o in L if o["takeover"]]
    print(f"  {t:26s} runs={len(L)} solved={sum(o['solved'] for o in L)} takeovers={len(tko)} takeover_solved={sum(o['solved'] for o in tko)}"
          f" mean$/run={f(sum(o['usd'] for o in L)/len(L))} | GLM alone={glm1.get(t)} | Opus alone={opus1.get(t, (None, 0))[0]}"
          f" ${f(opus1.get(t, (None, 0))[1])}")

print("\n=== Q3a solve paths (valid runs)")
paths = collections.Counter(); solves = collections.Counter(); usd = collections.defaultdict(float)
for o in V:
    if o["setup"] in ("glm", "opus"):
        p = "one session, one model (alone)"
    elif o["setup"].startswith("glmreview"):
        p = "one session + in-loop review (revise in same session)" if o["verdicts"][:1] == ["fix"] else "one session, review shipped/none"
    elif o["takeover"] and o["builder_fix"]:
        p = "builder -> new GLM fix session -> cross-model takeover"
    elif o["takeover"]:
        p = "handoff: GLM builder -> new takeover session (other model)"
    else:
        p = "one session, judge shipped / no judge"
    paths[p] += 1; solves[p] += o["solved"]; usd[p] += o["usd"]
for p in paths:
    print(f"  {p}: {solves[p]}/{paths[p]} solved, ${f(usd[p])}, ${f(usd[p] / max(solves[p], 1))}/solve")
print(f"  total solves {sum(solves.values())} in {len(V)} valid runs")

print("\n=== Q2e Opus-takeover setups only (28,29,31,34,35,36): rescue proxy and cost")
OT = [o for o in V if o["setup"] in ("takeover28", "takeover29", "takeover31", "takeover34", "takeover35", "takeover36")
      and o["takeover"]]
for lab, cond in (("task GLM alone failed", lambda o: glm1.get(o["task"]) is False),
                  ("task GLM alone solved", lambda o: glm1.get(o["task"]) is True)):
    L = [o for o in OT if cond(o)]
    jt = sum(o["usd_phase"]["takeover"] + o["usd_phase"]["review"] for o in L)
    s = sum(o["solved"] for o in L)
    print(f"  {lab}: {s}/{len(L)} takeover runs solved; judge+takeover ${f(jt)}; ${f(jt / max(s, 1))} per solve")
