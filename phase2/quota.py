"""Launch gate for paid batches: GLM runs on Ammar's Z.ai Coding Plan, so a batch stops launching new runs at a
share of the weekly quota (default 80%, PHASE2_GLM_WEEKLY_STOP) and waits out Z.ai peak hours (weekdays 14:00-18:00
Singapore = 10:00-14:00 Dubai, when calls cost more quota; PHASE2_OFFPEAK_ONLY=0 turns that off). Runs already
started are never killed. Agreed with Ibrahim 2026-11-18."""
import os
import re
import subprocess
import time
from datetime import datetime
from zoneinfo import ZoneInfo

QUOTA_RE = re.compile(r"^zai\s+5h\s+(\d+(?:\.\d+)?)%.*?weekly\s+(\d+(?:\.\d+)?)%", re.M)
_cache: dict = {}


def read_zai_quota(run=subprocess.run) -> tuple[float, float] | None:
    """(5-hour %, weekly %) from `ocx provider quota --refresh`, or None if it cannot be read."""
    try:
        out = run(["ocx", "provider", "quota", "--refresh"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = QUOTA_RE.search(out or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def zai_peak(now: datetime | None = None) -> bool:
    now = (now or datetime.now(ZoneInfo("Asia/Dubai"))).astimezone(ZoneInfo("Asia/Dubai"))
    return now.weekday() < 5 and 10 <= now.hour < 14


def launch_gate(now: datetime | None = None, quota=None) -> tuple[str, str]:
    """("go" | "wait" | "stop", reason). Quota is read at most every 5 minutes."""
    stop_at = float(os.environ.get("PHASE2_GLM_WEEKLY_STOP", "80"))
    if quota is None:
        if time.time() - _cache.get("t", 0) > 300:
            _cache.update(t=time.time(), q=read_zai_quota())
        quota = _cache.get("q")
    if quota is None:
        return "wait", "Z.ai quota unreadable (proxy down?)"
    if quota[1] >= stop_at:
        return "stop", f"GLM weekly quota {quota[1]:.0f}% >= {stop_at:.0f}%"
    if os.environ.get("PHASE2_OFFPEAK_ONLY", "1") != "0" and zai_peak(now):
        return "wait", "Z.ai peak hours (10:00-14:00 Dubai, weekdays)"
    return "go", f"GLM weekly {quota[1]:.0f}%, 5h {quota[0]:.0f}%"
