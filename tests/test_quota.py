"""Launch gate for paid batches (no network)."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from phase2 import quota

DXB = ZoneInfo("Asia/Dubai")


def test_parse_quota_line():
    out = "openai x\nzai 5h 7% resets 2026-11-18T16:24:02Z weekly 35% resets 2026-11-22T17:19:52Z\n"
    assert quota.read_zai_quota(run=lambda *a, **k: SimpleNamespace(stdout=out)) == (7.0, 35.0)
    assert quota.read_zai_quota(run=lambda *a, **k: SimpleNamespace(stdout="nothing")) is None


def test_peak_hours_dubai():
    assert quota.zai_peak(datetime(2026, 11, 18, 11, 0, tzinfo=DXB))      # Wednesday 11:00
    assert not quota.zai_peak(datetime(2026, 11, 18, 15, 0, tzinfo=DXB))  # Wednesday 15:00
    assert not quota.zai_peak(datetime(2026, 11, 21, 11, 0, tzinfo=DXB))  # Saturday


def test_gate(monkeypatch):
    off = datetime(2026, 11, 18, 15, 0, tzinfo=DXB)
    assert quota.launch_gate(off, quota=(5, 35))[0] == "go"
    assert quota.launch_gate(off, quota=(5, 80))[0] == "stop"
    assert quota.launch_gate(datetime(2026, 11, 18, 11, 0, tzinfo=DXB), quota=(5, 35))[0] == "wait"
    monkeypatch.setenv("PHASE2_OFFPEAK_ONLY", "0")
    assert quota.launch_gate(datetime(2026, 11, 18, 11, 0, tzinfo=DXB), quota=(5, 35))[0] == "go"
