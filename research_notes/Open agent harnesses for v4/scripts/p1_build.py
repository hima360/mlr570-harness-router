"""Plan/revise study, step 1: one row per dev run with plan size and per-phase cost (builder, strategist, review,
in-loop revise, builder fix, takeover). Reads runs.json from extract.py plus each trial's trace.jsonl.
Writes study.json. Read-only on the repo. Dev runs only (extract.py's filter: dt1 dev tasks + dv6..dv15)."""
import glob, json, os, re

ROOT = "/Users/hima/Developer/mlr570-harness-router/jobs/"
HERE = os.path.dirname(os.path.abspath(__file__))
PRICES = {"claude-opus-5-5": (4.0, 0.20, 20.0), "glm-5.3-flash": (0.15, 0.03, 0.50),
          "claude-sonnet-5": (2.0, 0.20, 10.0), "glm-5.3": (1.40, 0.26, 4.40)}
VERSION = {"glm": "GLM alone", "opus": "Opus alone", "glmreview26": "v2.6 in-loop review",
           "glmreview27": "v2.7 strategist+in-loop review+85% checkpoint", "takeover28": "v2.8 judge->Opus takeover",
           "takeover29": "v2.9 builder-fix-first", "takeover30": "v3.0 GLM-5.3 takeover",
           "takeover31": "v3.1 spec card+handoff note", "takeover34": "v3.4 low-effort takeover",
           "takeover35": "v3.5 cache kept", "takeover36": "v3.6 restatement", "takeover37": "v3.7 Sonnet-5 takeover"}


def cost(c):
    p = PRICES[c["model"].split("/")[-1]]
    i, k, o = c.get("input_tokens") or 0, c.get("cached_tokens") or 0, c.get("output_tokens") or 0
    return (max(i - k, 0) * p[0] + k * p[1] + o * p[2]) / 1e6


rows = json.load(open(f"{HERE}/runs.json"))
out = []
for r in rows:
    tp = glob.glob(ROOT + r["name"] + "/*/agent/trace.jsonl")
    ev = [json.loads(l) for l in open(tp[0]) if l.strip()] if tp else []
    plans = [e for e in ev if e["event"] == "plan"]
    reviews = [e for e in ev if e["event"] == "senior_review"]
    bfix = [e for e in ev if e["event"] == "builder_fix"]
    tko = [e for e in ev if e["event"] == "takeover"]
    strat = next((e for e in ev if e["event"] == "strategy"), None)
    t_review = reviews[0]["t"] if reviews else None
    t_bfix = next((e["t"] for e in bfix if e.get("phase") == "start"), None)
    t_tko = next((e["t"] for e in tko if e.get("phase") == "start"), None)
    in_loop = r["setup"].startswith("glmreview")
    ph = {"builder": 0.0, "strategist": 0.0, "spec": 0.0, "review": 0.0, "revise": 0.0, "builder_fix": 0.0,
          "takeover": 0.0, "opus_alone": 0.0}
    nc = {k: 0 for k in ph}
    # call events from the trace (with t) to place worker calls before/after the review or the fix
    for e in ev:
        if e["event"] != "llm_call":
            continue
        role, c = e["role"], cost(e)
        if role == "worker" and r["setup"] == "opus":
            k = "opus_alone"
        elif role == "worker":
            if in_loop and t_review is not None and e["t"] > t_review:
                k = "revise"  # same GLM session, after the reviewer sent problems back
            elif t_bfix is not None and e["t"] > t_bfix:
                k = "builder_fix"
            else:
                k = "builder"
        elif role == "reviewer":
            k = "review"
        elif role in ("strategist", "spec", "takeover"):
            k = role
        else:
            k = role
            ph.setdefault(k, 0.0); nc.setdefault(k, 0)
        ph[k] += c; nc[k] += 1
    verdicts = [e.get("verdict") or ("error" if e.get("error") else None) for e in reviews]
    tko_end = next((e for e in tko if e.get("phase") == "end"), None)
    bfix_end = next((e for e in bfix if e.get("phase") == "end"), None)
    out.append(dict(
        name=r["name"], prefix=r["prefix"], setup=r["setup"], version=VERSION.get(r["setup"], r["setup"]),
        task=r["task"], rep=r["rep"], exc=r["exc"], stopped=r["exc"] == "CancelledError",
        reward=r["reward"], solved=r["reward"] == 1, tests=r["tests"], usd=sum(ph.values()), usd_phase=ph,
        calls_phase=nc, has_trace=bool(ev),
        plan_steps=[len(p["steps"]) for p in plans], max_plan_steps=next((e.get("max_plan_steps") for e in ev
                                                                          if e["event"] == "config"), None),
        planner_calls=sum(1 for e in ev if e["event"] == "llm_call" and e["role"] == "planner"),
        strategy=({"mode": strat.get("mode"), "n_deliverables": len(strat.get("deliverables") or []),
                   "n_targets": len(strat.get("targets") or [])} if strat else None),
        verdicts=verdicts, n_problems=[len(e.get("problems") or []) for e in reviews],
        builder_exit=next((e.get("builder_exit") for e in reviews if e.get("builder_exit")), None),
        builder_fix=bool(bfix), builder_fix_exit=bfix_end.get("exit_status") if bfix_end else None,
        builder_fix_calls=bfix_end.get("n_calls") if bfix_end else None,
        takeover=bool(tko), takeover_model=(tko[0].get("model") if tko else None),
        takeover_exit=tko_end.get("exit_status") if tko_end else None,
        takeover_calls=tko_end.get("n_calls") if tko_end else None,
        handoff_note=[e.get("chars") for e in ev if e["event"] == "handoff"],
        trajs={k: v["cmds"] for k, v in r["trajs"].items()},
    ))
json.dump(out, open(f"{HERE}/study.json", "w"), default=str)
print(len(out), "runs; with trace", sum(o["has_trace"] for o in out))
