"""Rule 7: move every null row (passed=None) of results/mahek.jsonl, and its run folder, into runs/void-<reason>-mahek-1119/. Nothing is deleted.
Run from the repo root.  python runs\\void_nulls.py --dry   (preview)   |   python runs\\void_nulls.py   (do it)"""
import json, os, shutil, sys
DRY = "--dry" in sys.argv
LOG = os.path.join("results", "mahek.jsonl")
def reason(x):
    t, h, d = x["task"], x["harness"], x["duration_s"]
    if "webclients" in t: return "env-start-timeout"
    if t == "vpp-loss-divergence": return "memory-stopped"
    if t == "takens-embedding-lean" and h != "claude-code": return "network-install"
    if t == "ks-solver-cpp": return "instant-fail"
    if x.get("input_tokens") in (0, None) and 1000 < d < 1300 and h in ("claude-code", "codex"): return "agent-setup-timeout"
    return "setup-no-error"
rows = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]
keep, moved = [], []
for x in rows:
    (moved if x["passed"] is None else keep).append(x)
print(len(rows), "rows;", len(moved), "nulls to void;", len(keep), "stay")
bk = os.path.join("runs", "void-backup-mahek-1119"); os.makedirs(bk, exist_ok=True)
if not DRY: shutil.copy(LOG, os.path.join(bk, "mahek_before_void2.jsonl"))
for x in moved:
    r = reason(x); dest = os.path.join("runs", "void-%s-mahek-1119" % r)
    src = x["outdir"].replace("\\", os.sep).replace("/", os.sep)
    rel = src.split(os.sep, 2)[2] if src.startswith("runs" + os.sep + "mahek") else os.path.basename(src)
    print("%-22s %-11s %-30s -> %s" % (r, x["harness"], x["task"][:30], dest))
    if DRY: continue
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "voided_rows.jsonl"), "a", encoding="utf-8") as f: f.write(json.dumps(x) + "\n")
    if os.path.isdir(src):
        tgt = os.path.join(dest, rel); os.makedirs(os.path.dirname(tgt), exist_ok=True)
        if os.path.exists(tgt): tgt += "_2"
        shutil.move(src, tgt)
if not DRY:
    with open(LOG, "w", encoding="utf-8", newline="\n") as f:
        for x in keep: f.write(json.dumps(x) + "\n")
    print("done: results/mahek.jsonl now has", len(keep), "rows")
