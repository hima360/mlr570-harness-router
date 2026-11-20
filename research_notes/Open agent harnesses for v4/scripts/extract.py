"""Extract one row per dev run (dt1 + dv6..dv15, dev tasks only) into runs.json. Read-only on the repo."""
import collections, glob, json, os, re, sys
from datetime import datetime
ROOT = "/Users/hima/Developer/mlr570-harness-router"
sys.path.insert(0, ROOT)
from phase2.results import tests_fraction, split_name
from phase2.worker import INSTALL
from phase2.list_prices import call_usd as repo_call_usd

OUT = os.path.dirname(os.path.abspath(__file__))
DEV12 = ("cargo-flight-dispatch embedding-drift-monitor fin-saccr-rwa heat-pump-warranty html-js-filter kv-live-surgery "
         "mvcc-lsm-compaction payments-pipeline-fix react-lead-form risk-scorer-replay session-window-debug "
         "vf2-speedup-networkx").split()
EARLY_USED = ["cumulative-layout-shift", "data-anonymization", "embedding-drift-monitor", "heat-pump-warranty",
              "kv-live-surgery", "legacy-utility-triage", "music-harmony", "mvcc-lsm-compaction", "payments-pipeline-fix",
              "pretrain-shard-corruption", "session-window-debug", "shadow-relay", "telecom-entity-resolution",
              "html-js-filter", "interleaved-vigenere"]
DEVSET = set(DEV12) | set(EARLY_USED)
PREFIX = re.compile(r"^(dt1|dv([6-9]|1[0-5]))$")
# USD per M: (fresh input, cache read, output). Opus/Flash as given by the task; others from phase2/list_prices.py.
PRICES = {"claude-opus-5-5": (4.0, 0.20, 20.0), "glm-5.3-flash": (0.15, 0.03, 0.50),
          "claude-sonnet-5": (2.0, 0.20, 10.0), "glm-5.3": (1.40, 0.26, 4.40)}


def usd(model, inp, cached, out):
    p = PRICES[model.split("/")[-1]]
    inp, cached, out = inp or 0, cached or 0, out or 0
    fresh = max(inp - cached, 0)
    return fresh * p[0] / 1e6, cached * p[1] / 1e6, out * p[2] / 1e6


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def dur(r, k):
    v = r.get(k) or {}
    a, b = ts(v.get("started_at")), ts(v.get("finished_at"))
    return (b - a) if a and b else None


def commands(path):
    """(command, returncode, output) for each action in a mini-swe-agent trajectory."""
    d = json.load(open(path))
    msgs = d.get("messages", [])
    out, fmt_err = [], 0
    for i, m in enumerate(msgs):
        if m.get("role") == "user" and "EXACTLY ONE action" in str(m.get("content", "")):
            fmt_err += 1
        acts = ((m.get("extra") or {}).get("actions")) if m.get("role") == "assistant" else None
        if not acts:
            continue
        obs = msgs[i + 1].get("content", "") if i + 1 < len(msgs) else ""
        obs = obs if isinstance(obs, str) else json.dumps(obs)
        rc = re.search(r"<returncode>(-?\d+)</returncode>", obs)
        took = re.search(r"\[command took (\d+) s\]", obs)
        out.append({"cmd": acts[0].get("command", ""), "rc": int(rc.group(1)) if rc else None,
                    "timed_out": ("[timed out after" in obs) or ("timeout" in obs.lower() and "error occurred while executing" in obs.lower()),
                    "took": int(took.group(1)) if took else None, "exc": "error occurred while executing" in obs.lower(),
                    "obs_tail": obs[-400:]})
    return out, fmt_err, d.get("info", {}).get("exit_status")


rows, skipped = [], collections.Counter()
for d in sorted(glob.glob(f"{ROOT}/jobs/*/")):
    name = os.path.basename(d.rstrip("/"))
    if name.count("-") < 2:
        continue
    prefix, setup, task, rep = split_name(name)
    if not PREFIX.match(prefix):
        continue
    if task not in DEVSET:
        skipped[(prefix, "non-dev task")] += 1
        continue
    trials = [t for t in glob.glob(d + "*/") if os.path.exists(t + "result.json")]
    if not trials:
        skipped[(prefix, "no result.json")] += 1
        continue
    t = trials[0]
    r = json.load(open(t + "result.json"))
    exc = (r.get("exception_info") or {}).get("exception_type") or ""
    trp = t + "agent/trace.jsonl"
    ev = [json.loads(l) for l in open(trp) if l.strip()] if os.path.exists(trp) else []
    calls = [e for e in ev if e["event"] == "llm_call"]
    row = dict(name=name, prefix=prefix, setup=setup, task=task, rep=rep, exc=exc,
               reward=((r.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
               tests=tests_fraction(t), has_trace=bool(ev),
               t_total=(ts(r.get("finished_at")) or 0) - (ts(r.get("started_at")) or 0),
               t_env=dur(r, "environment_setup"), t_agent_setup=dur(r, "agent_setup"),
               t_agent=dur(r, "agent_execution"), t_verifier=dur(r, "verifier"))
    mu = (r.get("agent_result") or {}).get("model_usage") or {}
    row["model_usage"] = mu
    # cost from trace (per call) and from result.json model_usage (cross-check)
    cost = collections.defaultdict(float); parts = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    cost_role = collections.defaultdict(float); repo_cost = 0.0
    llm_ms = collections.defaultdict(float)
    callrows = []
    for e in calls:
        m = e["model"].split("/")[-1]
        f, c, o = usd(m, e.get("input_tokens"), e.get("cached_tokens"), e.get("output_tokens"))
        cost[m] += f + c + o; cost_role[e["role"]] += f + c + o
        for i, v in enumerate((f, c, o)):
            parts[m][i] += v
        repo_cost += repo_call_usd(e["model"], e.get("input_tokens"), e.get("cached_tokens"), e.get("output_tokens")) or 0
        llm_ms[e["role"]] += e.get("ms") or 0
        callrows.append({k: e.get(k) for k in ("t", "role", "model", "ms", "input_tokens", "cached_tokens",
                                                "output_tokens", "session", "guarded")})
    row.update(usd=sum(cost.values()), usd_model=dict(cost), usd_parts={k: v for k, v in parts.items()},
               usd_role=dict(cost_role), usd_repo=repo_cost, llm_ms=dict(llm_ms), calls=callrows)
    mu_cost = 0.0
    for m, u in mu.items():
        mm = m.split("/")[-1]
        if mm in PRICES:
            mu_cost += sum(usd(mm, u.get("n_input_tokens"), u.get("n_cache_tokens"), u.get("n_output_tokens")))
    row["usd_mu"] = mu_cost
    row["reviews"] = [{k: e.get(k) for k in ("round", "verdict", "builder_exit", "error", "t")} |
                      {"n_problems": len(e.get("problems") or [])} for e in ev if e["event"] == "senior_review"]
    row["takeovers"] = [{k: e.get(k) for k in ("phase", "model", "exit_status", "n_calls", "t")} for e in ev if e["event"] == "takeover"]
    row["builder_fix"] = [{k: e.get(k) for k in ("phase", "exit_status", "n_calls", "t")} for e in ev if e["event"] == "builder_fix"]
    row["attempts"] = [{k: e.get(k) for k in ("attempt", "model", "exit_status", "n_calls", "passed", "t")} for e in ev if e["event"] == "attempt"]
    row["call_failed"] = [{k: e.get(k) for k in ("role", "model", "kind", "will_retry")} | {"error": str(e.get("error"))[:200]}
                          for e in ev if e["event"] == "llm_call_failed"]
    row["explore"] = [e.get("command") for e in ev if e["event"] == "explore"]
    row["trace_end_t"] = ev[-1]["t"] if ev else None
    row["policy"] = next((e.get("policy") for e in ev if e["event"] == "config"), None)
    row["session_budget_chars"] = next((e.get("session_budget_chars") for e in ev if e["event"] == "config"), None)
    # trajectories
    trajs = {}
    for p in sorted(glob.glob(t + "agent/worker_*.json")):
        kind = os.path.basename(p)[:-5]
        try:
            cmds, fe, es = commands(p)
        except Exception as x:
            cmds, fe, es = [], 0, f"parse error {x}"
        trajs[kind] = {"cmds": cmds, "format_errors": fe, "exit": es}
    row["trajs"] = trajs
    rows.append(row)

json.dump(rows, open(f"{OUT}/runs.json", "w"), default=str)
print(len(rows), "runs;", "skipped:", dict(skipped))
