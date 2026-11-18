"""A5 offline router, step 1: early features of the GLM builder + per-run route outcomes.
Dev runs only (dt1 dev tasks, dv6..dv15). Reads runs.json from extract.py (same dev filter) and the GLM builder
trajectory / trace of each run. Never opens ho* or v4d1* jobs. Writes units.json next to this script."""
import glob, json, os, re, sys
ROOT = "/Users/hima/Developer/mlr570-harness-router"
HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = sys.argv[1] if len(sys.argv) > 1 else next(p for p in (os.path.join(HERE, "runs.json"), os.path.join(HERE, "..", "runs.json")) if os.path.exists(p))
PREFIX = re.compile(r"^(dt1|dv([6-9]|1[0-5]))$")
P = {"glm-5.3-flash": (0.15, 0.03, 0.50), "claude-opus-5-5": (4.0, 0.20, 20.0)}
TEST_CMD = re.compile(r"pytest|make test|npm (run )?test|cargo test|go test|unittest|ctest|jest|vitest|\./run_tests|bash tests?/")
WRITE = re.compile(r"(?:cat\s*>|tee\s+(?:-a\s+)?|>\s*)\s*['\"]?([\w./-]+\.\w+)")
SEDI = re.compile(r"sed\s+-i\S*\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+([\w./-]+)")
KS = (3, 5, 10)


def usd(m, inp, cached, out):
    p = P[m]; inp, cached, out = inp or 0, cached or 0, out or 0
    return (max(inp - cached, 0) * p[0] + cached * p[1] + out * p[2]) / 1e6


def traj(path):
    d = json.load(open(path)); msgs = d["messages"]; steps = []
    task_chars = len(str(msgs[1]["content"])) if len(msgs) > 1 else 0
    for i, m in enumerate(msgs):
        acts = (m.get("extra") or {}).get("actions") if m["role"] == "assistant" else None
        if not acts:
            continue
        o = msgs[i + 1] if i + 1 < len(msgs) else {}
        obs = o.get("content", ""); obs = obs if isinstance(obs, str) else json.dumps(obs)
        rc = re.search(r"<returncode>(-?\d+)</returncode>", obs)
        took = re.search(r"\[command took (\d+) s\]", obs)
        steps.append(dict(cmd=acts[0].get("command", ""), rc=int(rc.group(1)) if rc else None, obs=obs,
                          took=int(took.group(1)) if took else None, ts=(m.get("extra") or {}).get("timestamp")))
    return steps, task_chars


def feats(steps, calls, k, task_chars, n_tests_found):
    s = steps[:k]; n = max(len(s), 1); c = calls[:k]
    files = set()
    for x in s:
        files |= {f for f in WRITE.findall(x["cmd"]) if not f.startswith("/dev/")} | set(SEDI.findall(x["cmd"]))
    return {
        "fail_rate": sum(1 for x in s if x["rc"] not in (0, None)) / n,
        "not_found": sum(1 for x in s if "command not found" in x["obs"] or "No such file or directory" in x["obs"] and "python" in x["cmd"][:10]),
        "traceback": sum(1 for x in s if "Traceback (most recent call last)" in x["obs"]),
        "timeouts": sum(1 for x in s if "timed out" in x["obs"].lower()),
        "ran_tests": int(any(TEST_CMD.search(x["cmd"]) for x in s)),
        "files_touched": len(files),
        "wrote_tests": int(any("test" in f.lower() for f in files)),
        "mean_cmd_s": sum(x["took"] or 0 for x in s) / n,
        "mean_call_s": sum((x.get("ms") or 0) for x in c) / max(len(c), 1) / 1000,
        "mean_out_tok": sum((x.get("output_tokens") or 0) for x in c) / max(len(c), 1),
        "obs_kchars": sum(len(x["obs"]) for x in s) / n / 1000,
        "cmd_kchars": sum(len(x["cmd"]) for x in s) / n / 1000,
        "submitted_by_k": int(len(steps) <= k),
        "task_kchars": task_chars / 1000,
        "n_tests_found": n_tests_found,
    }


rows = json.load(open(RUNS)); units = []
for r in rows:
    if not PREFIX.match(r["prefix"]) or r["exc"] == "CancelledError" or r["reward"] is None:
        continue
    if not (r["setup"] == "glm" or r["setup"].startswith(("takeover", "glmreview"))):
        continue
    jd = glob.glob(f"{ROOT}/jobs/{r['name']}/*/agent/worker_s1_a1.json")
    if not jd:
        continue
    steps, task_chars = traj(jd[0])
    tr = os.path.join(os.path.dirname(jd[0]), "trace.jsonl")
    ev = [json.loads(l) for l in open(tr) if l.strip()] if os.path.exists(tr) else []
    tf = next((e.get("text", "") for e in ev if e["event"] == "tests_found"), "")
    n_tf = sum(1 for l in tf.splitlines() if l.startswith("- "))
    calls = [e for e in r["calls"] if e["role"] == "worker"]
    wcost = [usd(e["model"].split("/")[-1], e.get("input_tokens"), e.get("cached_tokens"), e.get("output_tokens")) for e in calls]
    tos = [t["model"] for t in r["takeovers"] if t["phase"] == "start"]
    verdicts = [v["verdict"] for v in r["reviews"] if v["verdict"]]
    u = dict(name=r["name"], prefix=r["prefix"], setup=r["setup"], task=r["task"], reward=r["reward"],
             usd=r["usd"], usd_role=r["usd_role"], worker_usd=sum(wcost), n_cmds=len(steps),
             takeover_model=tos[0].split("/")[-1] if tos else None, first_verdict=verdicts[0] if verdicts else None,
             cost_at_k={k: sum(wcost[:k]) + r["usd_role"].get("spec", 0) for k in KS},
             feats={k: feats(steps, calls, k, task_chars, n_tf) for k in KS})
    units.append(u)
json.dump(units, open(os.path.join(HERE, "units.json"), "w"), indent=0)
print(len(units), "units")
