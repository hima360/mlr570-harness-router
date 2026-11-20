"""What the app is connected to, without spending model quota on page load.

- opencodex proxy: GET /v1/models (free) and whether each configured model is listed.
- each model: last successful and last failed call seen in the newest traces.
- Z.ai quota: phase2.quota.read_zai_quota (5-hour and weekly %), cached 5 minutes.
- Docker: `docker info`, cached 30 seconds.
Status is green / amber / red with a plain reason.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from phase2 import quota as Q

from .launch import setup_args
from .models import model_info

OCX_URL = os.environ.get("RELAY_OCX_URL", "http://127.0.0.1:10100/v1")
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def cached(key: str, ttl: float, fn):
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


PENDING = object()
_inflight: set[str] = set()


def cached_bg(key: str, ttl: float, fn):
    """Like cached(), but a slow first read happens in the background: returns PENDING until it lands."""
    with _lock:
        hit = _cache.get(key)
        stale = not hit or time.time() - hit[0] >= ttl
        if stale and key not in _inflight:
            _inflight.add(key)

            def work():
                try:
                    val = fn()
                    with _lock:
                        _cache[key] = (time.time(), val)
                finally:
                    with _lock:
                        _inflight.discard(key)
            threading.Thread(target=work, daemon=True).start()
    return hit[1] if hit else PENDING


def ocx_models(url: str = OCX_URL, opener=urllib.request.urlopen) -> dict:
    t = time.time()
    try:
        req = urllib.request.Request(url.rstrip("/") + "/models", headers={"Authorization": "Bearer local"})
        with opener(req, timeout=3) as r:
            data = json.loads(r.read().decode())
        ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
        return {"ok": True, "ids": ids, "ms": round((time.time() - t) * 1000)}
    except Exception as e:  # noqa: BLE001  (any failure means "not reachable")
        return {"ok": False, "ids": [], "error": f"{type(e).__name__}: {e}"[:200]}


def docker_status(run=subprocess.run) -> dict:
    try:
        p = run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=6)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": f"{type(e).__name__}"}
    if p.returncode == 0 and p.stdout.strip():
        return {"ok": True, "version": p.stdout.strip()}
    return {"ok": False, "error": (p.stderr or p.stdout or "docker info failed").strip().splitlines()[-1][:200]}


def recent_model_activity(root: Path, n_traces: int = 40) -> dict[str, dict]:
    """{model id: {last_ok, last_fail, last_error}} from the newest traces (wall-clock seconds)."""
    paths = sorted(glob.glob(str(root / "jobs" / "*" / "*" / "agent" / "trace.jsonl")),
                   key=lambda p: os.path.getmtime(p), reverse=True)[:n_traces]
    out: dict[str, dict] = {}
    for p in paths:
        try:
            st = os.stat(p)
            lines = Path(p).read_text().splitlines()
        except OSError:
            continue
        t0 = getattr(st, "st_birthtime", None)
        rows = []
        for line in lines:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        if t0 is None:
            t0 = st.st_mtime - (float(rows[-1].get("t") or 0) if rows else 0)
        for e in rows:
            ev, model = e.get("event"), e.get("model")
            if ev not in ("llm_call", "llm_call_failed") or not model:
                continue
            when = t0 + float(e.get("t") or 0)
            rec = out.setdefault(model, {"last_ok": None, "last_fail": None, "last_error": ""})
            if ev == "llm_call":
                rec["last_ok"] = max(rec["last_ok"] or 0, when)
            else:
                if when > (rec["last_fail"] or 0):
                    rec["last_fail"], rec["last_error"] = when, str(e.get("error") or e.get("kind") or "")[:200]
    return out


def _config_models(root: Path, setup: str) -> list[tuple[str, str]]:
    config, _, _ = setup_args(setup)
    try:
        cfg = json.loads((root / config).read_text())
    except (OSError, ValueError):
        return []
    roles = []
    policy_tier = {"fixed:1": 1, "fixed:2": 2}.get(setup_args(setup)[1])
    tiers = cfg.get("tiers") or []
    if policy_tier is not None and policy_tier < len(tiers):
        roles.append((tiers[policy_tier], "builder"))
    if cfg.get("senior_reviewer_model"):
        roles.append((cfg["senior_reviewer_model"], "judge"))
    if cfg.get("takeover_model"):
        roles.append((cfg["takeover_model"], "takeover"))
    merged: dict[str, list[str]] = {}
    for m, r in roles:
        merged.setdefault(m, []).append(r)
    return [(m, " + ".join(rs)) for m, rs in merged.items()]


def ago(ts: float | None, now: float) -> str:
    if not ts:
        return "never"
    s = max(now - ts, 0)
    if s < 90:
        return "just now"
    if s < 5400:
        return f"{s / 60:.0f} min ago"
    if s < 172800:
        return f"{s / 3600:.0f} h ago"
    return f"{s / 86400:.0f} days ago"


def connections(root: Path, setup: str = "ocxv4", fresh: bool = False, probes=None) -> dict:
    """The connections panel. `probes` lets tests swap the network/subprocess checks."""
    probes = probes or {}
    if fresh:
        with _lock:
            _cache.pop("ocx", None); _cache.pop("docker", None)
    ocx = probes.get("ocx") or cached("ocx", 20, ocx_models)
    dock = probes.get("docker") or cached("docker", 30, docker_status)
    quota = probes["quota"] if "quota" in probes else cached_bg("quota", 300, Q.read_zai_quota)
    activity = probes.get("activity") or cached("activity", 60, lambda: recent_model_activity(root))
    now = time.time()
    items = []
    if ocx["ok"]:
        items.append({"id": "ocx", "name": "opencodex proxy", "status": "green",
                      "detail": f"Reachable at {OCX_URL}, {len(ocx['ids'])} models listed ({ocx.get('ms', 0)} ms)."})
    else:
        items.append({"id": "ocx", "name": "opencodex proxy", "status": "red",
                      "detail": f"Not reachable at {OCX_URL}: {ocx.get('error', '')}. Start it with `ocx start`."})
    for mid, role in _config_models(root, setup):
        info = model_info(mid)
        upstream = "/".join(mid.split("/")[1:]) if mid.startswith("ocx/") else mid
        act = activity.get(mid, {})
        ok_t, fail_t = act.get("last_ok"), act.get("last_fail")
        listed = upstream in ocx["ids"]
        if not ocx["ok"]:
            status, why = "red", "Proxy down, so this model cannot be reached."
        elif not listed:
            status, why = "red", f"opencodex does not list {upstream}."
        elif fail_t and (not ok_t or fail_t > ok_t):
            status, why = "amber", f"Listed. Last call failed {ago(fail_t, now)}: {act.get('last_error', '')}"
        elif ok_t:
            status, why = "green", f"Listed. Last successful call {ago(ok_t, now)}."
        else:
            status, why = "amber", "Listed, but no call seen in recent runs."
        items.append({"id": f"model:{mid}", "name": info["label"], "role": role, "provider": info["provider_label"],
                      "status": status, "detail": why, "last_ok": ok_t, "last_fail": fail_t})
    if quota is PENDING:
        items.append({"id": "zai", "name": "Z.ai quota (GLM)", "status": "amber",
                      "detail": "Reading the quota from opencodex (takes a few seconds)..."})
    elif quota is None:
        items.append({"id": "zai", "name": "Z.ai quota (GLM)", "status": "amber",
                      "detail": "Could not read the quota (`ocx provider quota`)."})
    else:
        five, week = quota
        stop = float(os.environ.get("PHASE2_GLM_WEEKLY_STOP", "80"))
        status = "red" if week >= stop else "amber" if week >= stop * 0.75 or five >= 80 else "green"
        why = f"5-hour window {five:.0f}% used, weekly {week:.0f}% used."
        if week >= stop:
            why += f" Batches stop launching at {stop:.0f}% weekly."
        if Q.zai_peak():
            status = "amber" if status == "green" else status
            why += " Z.ai peak hours now (calls cost more quota)."
        items.append({"id": "zai", "name": "Z.ai quota (GLM)", "status": status, "detail": why,
                      "five_hour": five, "weekly": week})
    items.append({"id": "docker", "name": "Docker", "status": "green" if dock["ok"] else "red",
                  "detail": f"Engine {dock['version']} running." if dock["ok"] else
                  f"Not running: {dock.get('error', '')}. Start Docker Desktop."})
    worst = "red" if any(i["status"] == "red" for i in items) else \
        "amber" if any(i["status"] == "amber" for i in items) else "green"
    return {"setup": setup, "overall": worst, "items": items, "checked": now}
