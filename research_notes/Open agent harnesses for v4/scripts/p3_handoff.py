"""Plan/revise study, step 3: information loss at handoffs (Q3b). For each agent session, classify commands as
writes vs reads (heuristic regexes below), then measure: commands before the first write (re-orientation cost),
re-reads of files the previous session already touched, full rewrites of files the previous session wrote, and
reverts (git checkout/restore/stash, copies from the original snapshot). Compares the Opus takeover (handoff
across models, fresh session + digest), the v2.9 GLM fix session (same model, fresh session + digest), the
in-loop revise (same GLM session, after the review message) and Opus alone (from scratch).
Reads study.json (p1_build.py). Heuristic: a command is a write if it redirects into a file, edits in place,
copies/moves, or runs python that opens a file for writing."""
import collections, json, os, re, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
S = [o for o in json.load(open(f"{HERE}/study.json")) if not o["stopped"] and o["has_trace"]]
ROOT = "/Users/hima/Developer/mlr570-harness-router/jobs/"

WRITE = re.compile(r"(?<![0-9&])>{1,2}\s*(?!/dev/null|&)[\w/.\-~$\"']|\btee\b|\bsed\s+-i|\bperl\s+-pi|\bcp\s|\bmv\s|"
                   r"\bpatch\b|git\s+apply|open\([^)]*['\"][wa]b?\+?['\"]|write_text|\.write\(|json\.dump\(|\.replace\(")
REVERT = re.compile(r"git\s+(checkout|restore|stash|reset)|/tmp/steps/acc/orig")
PATH = re.compile(r"(/(?:app|workspace|output|tmp)/[\w./\-]+)")
WPATH = re.compile(r"(?:>{1,2}\s*|tee\s+(?:-a\s+)?|sed\s+-i[^/]*?\s|open\(\s*['\"])(/(?:app|workspace|output)/[\w./\-]+)")


def paths(cmd):
    return {p.rstrip(".,;:'\")") for p in PATH.findall(cmd)}


def wpaths(cmd):
    out = {p.rstrip(".,;:'\")") for p in WPATH.findall(cmd)}
    for m in re.finditer(r"cat\s+>\s*(/(?:app|workspace|output)/[\w./\-]+)\s*<<", cmd):
        out.add(m.group(1))
    return out


def is_write(cmd):
    """A write to the task's files: a write-shaped command that is not only about scratch space in /tmp."""
    if not WRITE.search(cmd):
        return False
    task_side = re.search(r"/(app|workspace|output)\b", cmd)
    return bool(task_side)


def first_write(cmds):
    for i, c in enumerate(cmds):
        if is_write(c["cmd"] or ""):
            return i
    return None


def summ(name, vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return f"{name}: n=0"
    return f"{name}: n={len(vals)} median={st.median(vals)} mean={sum(vals)/len(vals):.1f} max={max(vals)}"


fw = collections.defaultdict(list); share_read = collections.defaultdict(list)
reread, rewrite, revert, overlap_rows = [], [], [], []
for o in S:
    tj = o["trajs"]
    if o["setup"] == "opus":
        c = tj.get("worker_s1_a1", [])
        fw["Opus alone (from scratch)"].append(first_write(c))
    if o["setup"] == "glm":
        fw["GLM alone (from scratch)"].append(first_write(tj.get("worker_s1_a1", [])))
    builder = tj.get("worker_s1_a1", [])
    if o["takeover"] and "worker_s1_a1_takeover" in tj:
        t = tj["worker_s1_a1_takeover"]
        key = f"takeover {o['takeover_model'].split('/')[-1]}" + (" +handoff note/spec (v3.1)" if o["setup"] == "takeover31" else "")
        fw[key].append(first_write(t))
        n = len(t) or 1
        share_read[key].append(sum(1 for c in t if not is_write(c["cmd"] or "")) / n)
        prev = list(builder) + list(tj.get("worker_s1_a1_fix", []))
        b_touch = set().union(*[paths(c["cmd"] or "") for c in prev]) if prev else set()
        b_write = set().union(*[wpaths(c["cmd"] or "") for c in prev]) if prev else set()
        fwi = first_write(t)
        orient = t[: fwi if fwi is not None else len(t)]
        rr = sum(1 for c in orient if paths(c["cmd"] or "") & b_touch)
        reread.append((o["name"], rr, len(orient)))
        rw = set().union(*[wpaths(c["cmd"] or "") for c in t]) & b_write if t else set()
        rewrite.append((o["name"], len(rw), len(b_write), o["solved"]))
        rv = [c["cmd"][:120] for c in t if REVERT.search(c["cmd"] or "")]
        revert.append((o["name"], len(rv), rv[:2], o["solved"]))
    if o["builder_fix"] and "worker_s1_a1_fix" in tj:
        fw["GLM fix session (v2.9, fresh session + digest)"].append(first_write(tj["worker_s1_a1_fix"]))

# in-loop revise: GLM commands after the review message in the same session
for o in S:
    if o["setup"].startswith("glmreview") and o["verdicts"][:1] == ["fix"]:
        wp = [p for p in os.popen(f"ls {ROOT}{o['name']}/*/agent/worker_s1_a1.json").read().split()]
        if not wp:
            continue
        msgs = json.load(open(wp[0])).get("messages", [])
        idx = next((i for i, m in enumerate(msgs) if m.get("role") == "user" and "NOT DONE YET" in str(m.get("content"))), None)
        if idx is None:
            continue
        after = []
        for m in msgs[idx + 1:]:
            acts = (m.get("extra") or {}).get("actions") if m.get("role") == "assistant" else None
            if acts:
                after.append({"cmd": acts[0].get("command", "")})
        fw["GLM in-loop revise (same session, after review)"].append(first_write(after))

print("=== commands before the first write (re-orientation), per session kind")
for k, v in fw.items():
    print("  " + summ(k, v), f"| never wrote: {sum(1 for x in v if x is None)}")
print("\n=== share of takeover commands that are read-only")
for k, v in share_read.items():
    print(f"  {k}: median {st.median(v):.2f} (n={len(v)})")
print("\n=== takeover orientation commands that touch a path the builder already touched")
tot_rr = sum(r[1] for r in reread); tot_o = sum(r[2] for r in reread)
print(f"  {tot_rr} of {tot_o} pre-first-write takeover commands ({tot_rr / max(tot_o, 1):.0%}) re-touch builder paths; "
      f"runs with any: {sum(1 for r in reread if r[1])}/{len(reread)}")
print("\n=== takeover rewrites of files the builder wrote (whole-file write to the same path)")
for name, n, nb, solved in rewrite:
    if n:
        print(f"  {name}: {n} of {nb} builder-written files rewritten; solved={solved}")
print(f"  runs with >=1 rewrite: {sum(1 for r in rewrite if r[1])}/{len(rewrite)}; solved among them "
      f"{sum(1 for r in rewrite if r[1] and r[3])}; solved among the rest {sum(1 for r in rewrite if not r[1] and r[3])}")
print("\n=== takeover reverts (git checkout/restore/stash/reset, copy from original snapshot)")
for name, n, ex, solved in revert:
    if n:
        print(f"  {name}: {n} solved={solved} e.g. {ex}")
print(f"  runs with a revert: {sum(1 for r in revert if r[1])}/{len(revert)}")
