"""Opus cache breaks (cached_tokens missing in trace = 0: llm.record_call drops zero fields). Cause buckets."""
import json,collections,glob,os
R="/Users/hima/Developer/mlr570-harness-router/jobs/"
rows=[r for r in json.load(open("runs.json")) if r["exc"]!="CancelledError"]
cfg={}
for r in rows:
    g=glob.glob(R+r["name"]+"/*/agent/trace.jsonl")
    if not g: continue
    tr=g[0]
    c=json.loads(open(tr).readline())
    cfg[r["setup"]]=(c.get("reasoning_effort"),c.get("takeover_effort"),c.get("session_budget_chars"),c.get("handoff"),c.get("session_nudge_chars"))
print("setup -> (reasoning_effort, takeover_effort, session_budget_chars, handoff, nudge):")
for k,v in cfg.items(): print("  ",k,v)
B=collections.defaultdict(lambda:[0,0.0]); n=collections.Counter(); fresh_all=collections.Counter()
for r in rows:
    prev={}
    for c in r["calls"]:
        if "opus" not in c["model"]: continue
        inp=c["input_tokens"] or 0; cac=c["cached_tokens"] or 0
        role=c["role"]; n[role]+=1; fresh_all[role]+=(inp-cac)*4/1e6
        p=prev.get(role); prev[role]=c
        if p is None: kind="first call of role (cold)"
        elif c["session"]=="new": kind="restart: harness started a new session"
        elif inp and cac/inp<0.5:
            if role=="reviewer": kind="reviewer: no session, prefix changed"
            elif inp < 0.8*(p["input_tokens"] or 0): kind="resume, input shrank (CLI-side compaction, inferred)"
            else: kind="resume, input grew, cache ~0 (cause not visible)"
        else: continue
        B[(kind,r["setup"] if "resume" in kind else "")][0]+=1; B[(kind,r["setup"] if "resume" in kind else "")][1]+=(inp-cac)*4/1e6
for k,v in sorted(B.items()): print(f"  {k[0]:58} {k[1]:12} n={v[0]:3} fresh ${v[1]:.2f}")
print("Opus fresh-input $ by role (all calls):",{k:round(v,2) for k,v in fresh_all.items()})
