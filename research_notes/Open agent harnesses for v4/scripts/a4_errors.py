"""Failed commands (rc != 0 or exec exception) by visible cause in the observation tail."""
import json,collections,re
rows=json.load(open("runs.json"))
PAT=[("submit rejected by gate (NOT DONE YET)",r"NOT DONE YET"),("exec exception (container/transport)",r"error occurred while executing"),
     ("timed out",r"\[timed out after|Timeout|timed out"),("python Traceback",r"Traceback \(most recent"),
     ("SyntaxError/IndentationError",r"SyntaxError|IndentationError"),("command not found",r"command not found|not found"),
     ("No such file",r"No such file"),("grep no match / test false (rc1, empty-ish)",r"^$"),("assert/FAILED",r"AssertionError|FAILED|assert")]
C=collections.Counter(); byrole=collections.Counter(); ex=collections.defaultdict(list); rcs=collections.Counter()
for r in rows:
    for kind,tj in r["trajs"].items():
        role="takeover" if kind.endswith("takeover") else "builder"
        for c in tj["cmds"]:
            if c["rc"] in (0,None) and not c["exc"]: continue
            rcs[c["rc"]]+=1
            tail=c["obs_tail"]
            body=re.sub(r"</?output>|<returncode>-?\d+</returncode>","",tail).strip()
            k="other"
            for name,p in PAT:
                if name.startswith("grep"):
                    if c["rc"]==1 and len(body)<40: k=name;break
                    continue
                if re.search(p,tail): k=name;break
            C[k]+=1; byrole[(role,k)]+=1
            if len(ex[k])<4: ex[k].append((r["name"],c["cmd"][:60].replace("\n"," "),body[-120:].replace("\n"," ")))
print("return codes:",rcs.most_common(10))
for k,v in C.most_common(): print(f"{k:45} {v}")
print({k:v for k,v in byrole.items()})
for k,v in ex.items():
    print("--",k)
    for x in v: print("   ",x)
# gate rejections per run
g=collections.Counter()
for r in rows:
    for kind,tj in r["trajs"].items():
        g[r["name"]]+=sum("NOT DONE YET" in c["obs_tail"] for c in tj["cmds"])
print("gate rejections: total",sum(g.values()),"runs with any",sum(1 for v in g.values() if v), g.most_common(5))
