"""Per-stage comparison v4 (v4d6) vs v2.8 (dv8): builder, judge, takeover. Run from repo root."""
import glob, json, statistics as st

def stages(d):
    f = glob.glob(d + "/*/agent/trace.jsonl")
    if not f or not glob.glob(d + "/*/result.json"):
        return None
    r = json.load(open(glob.glob(d + "/*/result.json")[0]))
    ev = [json.loads(l) for l in open(f[0])]
    out = dict(reward=((r.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
               b_calls=0, b_out=0, b_sec=0, j_calls=0, j_out=0, j_explored=0, t_calls=0, t_out=0, t_in=0, t_sec=0,
               takeover=0, verdict=None, bexit=None)
    for e in ev:
        k = e.get("event")
        if k == "senior_review" and out["verdict"] is None:
            out.update(verdict=e.get("verdict"), bexit=e.get("builder_exit"), j_explored=e.get("explored") or 0)
        if k == "takeover" and e.get("phase") == "start":
            out["takeover"] = 1
        if k != "llm_call":
            continue
        role = e.get("role")
        p = {"worker": "b", "reviewer": "j", "takeover": "t"}.get(role)
        if not p:
            continue
        out[p + "_calls"] += 1
        out[p + "_out"] += e.get("output_tokens") or 0
        if p == "t":
            out["t_in"] += e.get("input_tokens") or 0
        if p in "bt":
            out[p + "_sec"] += (e.get("ms") or 0) / 1000
    return out

def summarize(name, rows):
    rows = [r for r in rows if r]
    tk = [r for r in rows if r["takeover"]]
    med = lambda xs: round(st.median(xs)) if xs else 0
    print(f"{name:6s} runs={len(rows)} solves={sum(r['reward'] == 1 for r in rows)} "
          f"builder: calls {med([r['b_calls'] for r in rows])} out_tok {med([r['b_out'] for r in rows])} "
          f"min {med([r['b_sec'] / 60 for r in rows])} submitted {sum(r['bexit'] == 'Submitted' for r in rows)} | "
          f"judge: explored {med([r['j_explored'] for r in rows])} out_tok {med([r['j_out'] for r in rows])} fix {sum(r['verdict'] == 'fix' for r in rows)} | "
          f"takeover n={len(tk)}: calls {med([r['t_calls'] for r in tk])} out_tok {med([r['t_out'] for r in tk])} "
          f"in_tok {med([r['t_in'] for r in tk])} solved {sum(r['reward'] == 1 for r in tk)}")

tasks = sorted({p.split("-ocxv4-")[1].rsplit("-r", 1)[0] for p in glob.glob("jobs/v4d6-ocxv4-*") if "." not in p.split("/")[-1]})
v28 = [stages(f"jobs/dv8-takeover28-{t}") for t in tasks]
v35 = [stages(f"jobs/dv13-takeover35-{t}") for t in tasks]
v4 = [stages(f"jobs/v4d6-ocxv4-{t}-r{i}") for t in tasks for i in (1, 2)]
summarize("v2.8", v28); summarize("v3.5", v35); summarize("v4", v4)
