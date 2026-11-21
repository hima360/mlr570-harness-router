"""Opus cache: separate missing cached_tokens (None) from real breaks; break cause = new session vs resume with low hit."""
import json,collections,statistics as st
rows=[r for r in json.load(open("runs.json")) if r["exc"]!="CancelledError"]
miss=collections.Counter(); missin=collections.Counter()
for r in rows:
    for c in r["calls"]:
        if c["cached_tokens"] is None:
            miss[(r["setup"],c["model"].split('/')[-1],c["role"])]+=1; missin[r["setup"]]+= (c["input_tokens"] or 0)*(4 if "opus" in c["model"] else 0)/1e6
print("calls with cached_tokens missing:",dict(miss)); print("their input priced fully fresh ($, Opus only):",{k:round(v,2) for k,v in missin.items()})
B=collections.defaultdict(lambda:[0,0.0]); tot=collections.Counter()
for r in rows:
    seen=set()
    for c in r["calls"]:
        if "opus" not in c["model"] or c["cached_tokens"] is None: continue
        inp,cac=c["input_tokens"] or 0,c["cached_tokens"]
        tot[c["role"]]+=1
        first=c["role"] not in seen; seen.add(c["role"])
        if c["session"]=="new" and not first: k="restart (new session mid-role)"
        elif c["session"]=="resume" and inp and cac/inp<0.5: k="resume, hit<50%"
        elif c["session"] is None and inp and cac/inp<0.5 and not first: k="no session, hit<50%"
        else: continue
        B[(r["setup"],c["role"],k)][0]+=1; B[(r["setup"],c["role"],k)][1]+=(inp-cac)*4/1e6
print("Opus calls with known cache:",dict(tot))
s=0
for k,v in sorted(B.items()):
    print(k,v[0],round(v[1],2)); s+=v[1]
print("total break fresh $",round(s,2))
# reviewer: per-review first call vs later calls hit
hits=collections.defaultdict(list)
for r in rows:
    rc=[c for c in r["calls"] if c["role"]=="reviewer" and c["cached_tokens"] is not None and c["input_tokens"]]
    for i,c in enumerate(rc): hits["first" if i==0 else "later"].append(c["cached_tokens"]/c["input_tokens"])
for k,v in hits.items(): print("reviewer",k,"n",len(v),"median hit",round(st.median(v),2),"share<0.5",round(sum(x<.5 for x in v)/len(v),2))
