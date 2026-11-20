"""Plan/revise study, step 4: task features vs where revise / takeover paid off (Q4), plus the Opus cost of the
takeover's re-orientation (calls before its first write to task files, Q3b). Reads study.json and runs.json.
TYPE labels are this study's own reading of each task instruction (inference). Deliverable counts are the v2.7
strategist's (Opus) list for the task, one run each (dt1, or dv7 where dt1 had none)."""
import collections, json, os, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
S = [o for o in json.load(open(f"{HERE}/study.json")) if not o["stopped"] and o["has_trace"]]
R = {r["name"]: r for r in json.load(open(f"{HERE}/runs.json"))}
PRICES = {"claude-opus-5-5": (4.0, 0.20, 20.0), "glm-5.3-flash": (0.15, 0.03, 0.50),
          "claude-sonnet-5": (2.0, 0.20, 10.0), "glm-5.3": (1.40, 0.26, 4.40)}


def cost(c):  # same price table as p1_build.py
    p = PRICES[c["model"].split("/")[-1]]
    i, k, o = c.get("input_tokens") or 0, c.get("cached_tokens") or 0, c.get("output_tokens") or 0
    return (max(i - k, 0) * p[0] + k * p[1] + o * p[2]) / 1e6

TYPE = {  # inference from the instruction text
    "cargo-flight-dispatch": "repair", "embedding-drift-monitor": "repair", "session-window-debug": "repair",
    "react-lead-form": "repair", "mvcc-lsm-compaction": "repair", "pretrain-shard-corruption": "repair",
    "risk-scorer-replay": "repair", "data-anonymization": "build", "html-js-filter": "build",
    "interleaved-vigenere": "build", "vf2-speedup-networkx": "build", "kv-live-surgery": "service",
    "payments-pipeline-fix": "service", "fin-saccr-rwa": "answer/operate", "heat-pump-warranty": "answer/operate",
    "legacy-utility-triage": "answer/operate", "telecom-entity-resolution": "answer/operate"}
deliv = {}
for o in sorted(S, key=lambda o: o["prefix"] != "dt1"):
    if o["strategy"] and o["task"] not in deliv:
        deliv[o["task"]] = o["strategy"]["n_deliverables"]

OPUS_TK = ("takeover28", "takeover29", "takeover31", "takeover34", "takeover35", "takeover36")
rows = {}
for t in sorted({o["task"] for o in S}):
    L = [o for o in S if o["task"] == t]
    g = [o for o in L if o["setup"] == "glm"]; op = [o for o in L if o["setup"] == "opus"]
    rv = [o for o in L if o["setup"].startswith("glmreview") and o["verdicts"][:1] == ["fix"]]
    tk = [o for o in L if o["setup"] in OPUS_TK and o["takeover"]]
    sh = [o for o in L if o["setup"].startswith("takeover") and not o["takeover"] and o["verdicts"]]
    rows[t] = dict(type=TYPE.get(t, "?"), deliv=deliv.get(t), glm=[o["solved"] for o in g], opus=[o["solved"] for o in op],
                   rev=(sum(o["solved"] for o in rv), len(rv)), tk=(sum(o["solved"] for o in tk), len(tk)),
                   tk_usd=sum(o["usd"] for o in tk), ship=(sum(o["solved"] for o in sh), len(sh)))
print("=== per task (GLM alone / Opus alone = dt1, n=1 each; rev = in-loop review 'fix' runs; tk = Opus takeovers)")
print(f"  {'task':27s} {'type':15s} deliv glm   opus  rev   tk    tk$/run ship")
for t, r in rows.items():
    print(f"  {t:27s} {r['type']:15s} {str(r['deliv']):5s} {str(r['glm']):5s} {str(r['opus']):5s} "
          f"{r['rev'][0]}/{r['rev'][1]}   {r['tk'][0]}/{r['tk'][1]}   {r['tk_usd']/max(r['tk'][1],1):.2f}    {r['ship'][0]}/{r['ship'][1]}")


def agg(key):
    G = collections.defaultdict(lambda: collections.Counter())
    for t, r in rows.items():
        k = key(t, r)
        if k is None:
            continue
        c = G[k]
        c["tasks"] += 1
        c["glm_s"] += sum(r["glm"]); c["glm_n"] += len(r["glm"])
        c["opus_s"] += sum(r["opus"]); c["opus_n"] += len(r["opus"])
        c["rev_s"] += r["rev"][0]; c["rev_n"] += r["rev"][1]
        c["tk_s"] += r["tk"][0]; c["tk_n"] += r["tk"][1]
        if r["glm"] and not any(r["glm"]):
            c["tk_rescue_s"] += r["tk"][0]; c["tk_rescue_n"] += r["tk"][1]
            c["rev_rescue_s"] += r["rev"][0]; c["rev_rescue_n"] += r["rev"][1]
    for k, c in sorted(G.items(), key=str):
        print(f"  {k}: tasks={c['tasks']} | GLM alone {c['glm_s']}/{c['glm_n']} | Opus alone {c['opus_s']}/{c['opus_n']}"
              f" | in-loop revise {c['rev_s']}/{c['rev_n']} | Opus takeover {c['tk_s']}/{c['tk_n']}"
              f" | on GLM-alone-failed tasks: takeover {c['tk_rescue_s']}/{c['tk_rescue_n']}, revise {c['rev_rescue_s']}/{c['rev_rescue_n']}")


print("\n=== by task type (inference)")
agg(lambda t, r: r["type"])
print("\n=== by strategist deliverable count")
agg(lambda t, r: None if r["deliv"] is None else ("1-2 deliverables" if r["deliv"] <= 2 else "3+ deliverables"))

# --- takeover re-orientation cost (Q3b): Opus calls before its first write to task files
import re
WRITE = re.compile(r"(?<![0-9&])>{1,2}\s*(?!/dev/null|&)[\w/.\-~$\"']|\btee\b|\bsed\s+-i|\bperl\s+-pi|\bcp\s|\bmv\s|"
                   r"\bpatch\b|git\s+apply|open\([^)]*['\"][wa]b?\+?['\"]|write_text|\.write\(|json\.dump\(|\.replace\(")


def is_write(cmd):
    return bool(WRITE.search(cmd)) and bool(re.search(r"/(app|workspace|output)\b", cmd))


tot = []; share = []
for o in S:
    if o["setup"] in OPUS_TK and o["takeover"] and "worker_s1_a1_takeover" in o["trajs"]:
        t = o["trajs"]["worker_s1_a1_takeover"]
        k = next((i for i, c in enumerate(t) if is_write(c["cmd"] or "")), len(t))
        calls = [c for c in R[o["name"]]["calls"] if c["role"] == "takeover"]
        pre = sum(cost(c) for c in calls[:k]); all_ = sum(cost(c) for c in calls)
        tot.append(pre); share.append(pre / all_ if all_ else 0)
print(f"\n=== Opus takeover re-orientation (calls before first task-file write; approximates call i = command i)")
print(f"  n={len(tot)} takeovers; pre-write Opus $ total {sum(tot):.2f}; median per takeover {st.median(tot):.3f};"
      f" median share of takeover $ {st.median(share):.2f}")
