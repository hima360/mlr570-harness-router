"""Failure categories of unsolved runs; tool/command usage from mini-swe-agent trajectories."""
import json, os, re, sys, collections, statistics as st
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/Users/hima/Developer/mlr570-harness-router")
from phase2.worker import INSTALL
rows = json.load(open(f"{H}/runs.json"))

# ---------- failure categories ----------
print("== Unsolved runs ==")
cat = collections.Counter(); ex = collections.defaultdict(list)
for r in rows:
    if r["reward"] == 1:
        continue
    toks = sum((c["input_tokens"] or 0) + (c["output_tokens"] or 0) for c in r["calls"])
    att = r["attempts"]; bexit = att[0]["exit_status"] if att else None
    tend = [x for x in r["takeovers"] if x["phase"] == "end"]
    texit = tend[0]["exit_status"] if tend else None
    verdicts = [x["verdict"] for x in r["reviews"] if x.get("verdict")]
    tests = r["tests"] if r["tests"] is not None else 0
    if r["exc"] == "CancelledError":
        k = "stopped by operator (CancelledError)"
    elif r["exc"]:
        k = f"infra: Harbor exception {r['exc']}"
    elif toks == 0:
        k = "infra: 0-token run"
    elif "ProviderUnavailable" in (bexit, texit) or any(f["kind"] == "provider_unavailable" for f in r["call_failed"]):
        k = "infra: provider unavailable"
    elif texit in ("RuntimeError", "RepeatedFormatError"):
        k = f"tool/format error ending takeover ({texit})"
    elif bexit in ("TimeExceeded", "LimitsExceeded", "ModelTimedOut") and not r["takeovers"]:
        k = f"builder hit limit ({bexit}), no takeover"
    elif bexit == "RepeatedFormatError" and not r["takeovers"]:
        k = "builder RepeatedFormatError, no takeover"
    elif tests >= 0.8:
        k = "near miss: >=80% tests passed"
    elif tests >= 0.5:
        k = "partial: 50-80% tests"
    else:
        k = "far: <50% tests"
    sub = ("judge shipped" if verdicts and verdicts[-1] == "ship" else "after takeover" if r["takeovers"]
           else "builder limit" if bexit in ("TimeExceeded", "LimitsExceeded") else "single agent")
    cat[k] += 1; ex[k].append(f"{r['name']}({tests:.2f},{sub})")
n = sum(cat.values())
for k, v in cat.most_common():
    print(f"  {k:48} {v:3} ({v/n:.0%})")
print(f"  total unsolved {n}")
for k in ex:
    print(f"\n  [{k}]\n    " + "\n    ".join(ex[k]))

# builder exit status, all runs
print("\nbuilder exit_status (first attempt), valid runs:", dict(collections.Counter(
    (r["attempts"][0]["exit_status"] if r["attempts"] else "none") for r in rows if r["exc"] != "CancelledError")))
lim = [r for r in rows if r["attempts"] and r["attempts"][0]["exit_status"] in ("TimeExceeded", "LimitsExceeded")]
print("runs with builder at limit:", [(r["name"], r["attempts"][0]["exit_status"], r["reward"],
                                        bool(r["takeovers"])) for r in lim])

# ---------- tools ----------
print("\n== Commands (mini-swe-agent trajectories, all dev runs incl. stopped) ==")
def head(cmd):
    c = re.sub(r"^\s*#\s*timeout:\s*\d+\s*\n", "", cmd).strip()
    c = re.sub(r"^(cd\s+\S+\s*(&&|;)\s*)+", "", c)
    w = c.split()
    if not w:
        return "(empty)"
    if w[0] in ("python", "python3") and len(w) > 1:
        return f"{w[0]} {'-c/heredoc' if w[1] in ('-c', '-', '<<') or w[1].startswith('<<') else ('-m ' + w[2] if w[1] == '-m' and len(w) > 2 else 'script')}"
    if w[0] == "cat" and "<<" in c:
        return "cat heredoc (write file)"
    return w[0]
C = collections.Counter(); F = collections.Counter(); TO = []; inst = []; rep = collections.Counter(); rep_ex = []
by_kind = collections.Counter(); kind_fail = collections.Counter(); kind_n = collections.Counter()
took = collections.Counter(); fmt = collections.Counter(); ncmd = 0; slow = []
inst_runs = collections.Counter(); inst_time = 0; inst_n_took = 0; already = 0
for r in rows:
    builder_installs = set()
    for kind, tj in r["trajs"].items():
        role = "takeover" if kind.endswith("takeover") else ("builder_fix" if "fix" in kind else "builder")
        model = ("opus" if r["setup"] == "opus" else "glm-flash") if role.startswith("builder") else \
            next((x["model"].split("/")[-1] for x in r["takeovers"] if x["phase"] == "start"), "?")
        fmt[model] += tj["format_errors"]
        seen = collections.Counter()
        for c in tj["cmds"]:
            ncmd += 1; h = head(c["cmd"]); C[h] += 1; kind_n[model] += 1
            if c["rc"] not in (0, None) or c["exc"]:
                F[h] += 1; kind_fail[model] += 1
            if c["timed_out"] or c["rc"] == 124:
                TO.append((r["name"], role, c["cmd"][:90].replace("\n", " "), c["took"]))
            if c["took"]:
                took[h] += c["took"]
                if c["took"] >= 120:
                    slow.append((c["took"], r["name"], role, c["cmd"][:80].replace("\n", " ")))
            key = c["cmd"].strip()
            seen[key] += 1
            if seen[key] == 2:
                rep[h] += 1
                rep_ex.append((r["name"], model, key[:70].replace("\n", " ")))
            if INSTALL.search(c["cmd"]):
                inst.append((r["name"], role, c["cmd"][:100].replace("\n", " "), c["took"]))
                inst_runs[r["name"]] += 1
                inst_time += c["took"] or 0; inst_n_took += bool(c["took"])
                if "already satisfied" in c["obs_tail"].lower() or "already the newest" in c["obs_tail"].lower():
                    already += 1
print(f"commands total {ncmd}")
print("top command heads:", C.most_common(25))
print("failed (rc!=0) by head:", F.most_common(15))
print("failure rate by model:", {m: f"{kind_fail[m]}/{kind_n[m]} = {kind_fail[m]/kind_n[m]:.0%}" for m in kind_n})
print("format errors (no/extra action) by model:", dict(fmt))
print(f"timed-out commands: {len(TO)}")
for x in TO:
    print("   ", x)
print("seconds in commands >=15 s, by head (only slow commands report time):", took.most_common(12))
print(f"commands >=120 s: {len(slow)}")
for x in sorted(slow, reverse=True)[:15]:
    print("   ", x)
print(f"\ninstall commands {len(inst)} in {len(inst_runs)} runs of {len(rows)}; seconds reported (>=15s ones) {inst_time} "
      f"over {inst_n_took} cmds; output tail says already satisfied/newest: {already}")
print("installs per run:", inst_runs.most_common(12))
for x in inst[:40]:
    print("   ", x)
print(f"\nrepeated identical commands (2nd+ occurrence within one agent): {sum(rep.values())}; by head {rep.most_common(10)}")
rr = collections.Counter((a, b) for a, b, c in rep_ex)
print("runs with most repeats:", rr.most_common(8))
for x in rep_ex[:25]:
    print("   ", x)
