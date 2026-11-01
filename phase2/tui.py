"""Terminal UI for the routed harness: start runs, watch who does what live, browse and stop jobs.

  .venv/bin/python -m phase2.tui        (or double-click Harness.command)

Keys: n new run, c compare results, / filter jobs, s stop the selected run, r refresh, ? help, q quit.
Everything shown comes from the run's own records (trace.jsonl, worker trajectories, Harbor results),
so the planner, router and worker names are the ones that actually ran, not the config defaults.
"""
import re
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (Button, DataTable, Footer, Header, Input, Label, ListItem, ListView, RichLog,
                             Select, Static, TabbedContent, TabPane, Tree)

from . import jobs as J
from . import results as R
from .setups import SETUPS
from .delegate import parse_delegate
from .list_prices import trace_usd

ROOT = Path(__file__).resolve().parents[1]
# One design for the TUI, the HTML report and the deck: ink and paper with a terracotta accent, calm status colours.
OK, BAD, WARN, MUTED, ACCENT = "#7fb685", "#e07a68", "#e2b55c", "#8b93a7", "#d99a5b"
THEME = Theme(name="mlr570", primary=ACCENT, secondary="#8fb0d9", accent=ACCENT, warning=WARN, error=BAD,
              success=OK, foreground="#e6e1d6", background="#11161f", surface="#161d29", panel="#1f2838", dark=True)
STATUS_STYLE = {"running": "bold #e2b55c", "passed": "bold #7fb685", "failed": "bold #e07a68", "error": "bold #e07a68",
                "stopped": "dim", "incomplete": "dim"}
STATUS_ICON = {"running": "●", "passed": "✓", "failed": "✗", "error": "!", "stopped": "■", "incomplete": "…"}
STATUS_WORD = {"running": "running", "passed": "solved", "failed": "not solved", "error": "error", "stopped": "stopped",
               "incomplete": "incomplete"}
ROLE_STYLE = {"PLANNER": "bold #b6a0e0", "ROUTER": "bold #8fb0d9", "WORKER": "bold #8fb0d9", "VERIFIER": "bold #7fb685",
              "ESCALATE": "bold #e2b55c", "DONE": "bold #e6e1d6", "LEAD": "bold #8fb0d9", "HELPER": "bold #d99a5b",
              "REVIEWER": "bold #b6a0e0"}
LEGACY_ROUTER = {"laya": "Laya (laya_mlx, local MLX)", "arch": "Arch-Router-1.5B (katanemo, local)",
                 "fixed": "fixed policy (no model)", "random": "random policy (no model)"}
EXIT_REASON = {"StepIdle": "no model reply for too long", "TimeExceeded": "hit the step's time cap",
               "LimitsExceeded": "used all its turns", "RepeatedFormatError": "kept replying in the wrong format",
               "RuntimeError": "a model call kept failing"}
CMD_RE = re.compile(r"```mswea_bash_command\s*(.*?)(?:```|$)", re.S)


# ---------- pure renderers (tested without a terminal) ----------

def job_matches(name: str, status: str, query: str) -> bool:
    """Sidebar filter: every space-separated word must appear in the job name or its status."""
    hay = f"{name} {status}".lower()
    return all(w in hay for w in query.lower().split())


def setup_values(setup: str, configs: list[str]) -> dict:
    """What choosing an evaluation setup fills in on the New run screen: its policy, caps and config."""
    policy, caps, *cfg = SETUPS[setup]
    out = {"policy": policy, "caps": dict(caps)}
    if cfg and cfg[0] in configs:
        out["config"] = cfg[0]
    return out


def task_choices(dataset: Path = Path.home() / "Developer/harbor-datasets/terminal-bench") -> list[tuple]:
    """The curated tasks first, then every Terminal-Bench task found in the local dataset copy."""
    known = {v for _, v in J.TASKS}
    found = sorted(d.name for d in dataset.iterdir() if (d / "task.toml").exists()) if dataset.is_dir() else []
    return J.TASKS + [(f"tb4: {t}", f"terminal-bench/{t}") for t in found if f"terminal-bench/{t}" not in known]


def job_label(name: str, status: str, reward=None) -> Text:
    """Sidebar entry: the task on top, then evaluation · setup · repeat · outcome, dimmed."""
    try:
        prefix, setup, task, rep = R.split_name(name)
        top, sub = task, f"{prefix} · {setup} · r{rep}"
    except ValueError:
        top, sub = name, ""
    t = Text()
    t.append(f"{STATUS_ICON.get(status, '?')} ", style=STATUS_STYLE.get(status, ""))
    t.append(_short(top, 34), style="bold")
    word = STATUS_WORD.get(status, status)
    t.append(f"\n  {sub + ' · ' if sub else ''}{word}", style=MUTED)
    return t


def limits_text(caps: dict) -> str:
    """'1 step · 80 commands · 45 min per step' from a run's caps."""
    bits = []
    if caps.get("max_plan_steps"):
        n = int(caps["max_plan_steps"])
        bits.append(f"{n} step" + ("s" if n != 1 else "") + (" max" if n > 1 else ""))
    if caps.get("worker_step_limit"):
        bits.append(f"{caps['worker_step_limit']} commands")
    if caps.get("step_wall_time_sec"):
        bits.append(f"{int(caps['step_wall_time_sec']) // 60} min per step")
    return " · ".join(bits)


def results_header(p: dict) -> Text:
    eta = time.strftime("%H:%M", time.localtime(p["eta"])) if p.get("eta") else "-"
    t = Text(f"{p['done']} of {p['total']} runs done", style="bold")
    t.append(f"   ·   {p['running']} running   ·   {p['queued']} queued   ·   finish about {eta}", style=MUTED)
    return t


SETUP_COLUMNS = ("Setup", "Solved", "Tests passed", "Tokens", "Cost", "Cost per solve", "Min per run",
                 "Hit a limit", "Reviewed")


def setup_rows(p: dict) -> list[tuple]:
    """One row per setup; the best (most solved, then cheapest) in the success colour."""
    if not p["setups"]:
        return []
    best = max(p["setups"].items(), key=lambda kv: (kv[1]["passed"], -kv[1]["usd"]))[0]
    rows = []
    for s, a in p["setups"].items():
        style = f"bold {OK}" if s == best else ""
        per = "-" if a["passed"] == 0 else f"${a['usd_per_solve']:.2f}"
        cells = (s, f"{a['passed']} / {a['runs']}", f"{a['mean_tests']:.0%}", f"{a['tokens'] / 1e6:.1f}M",
                 f"${a['usd']:.2f}", per, f"{a['minutes_per_run']:.0f}", str(a["limit_hits"]), str(a["reviewed"]))
        rows.append(tuple(Text(c, style=style, justify="left" if i == 0 else "right") for i, c in enumerate(cells)))
    return rows


def task_rows(p: dict) -> tuple[list[str], list[tuple]]:
    """Columns (Task + one per setup) and rows: each run's share of hidden tests passed, ✓ when solved."""
    setups = list(p["setups"]) + sorted({r["setup"] for r in p["rows"]} - set(p["setups"]))
    cells: dict = {}
    for r in sorted(p["rows"], key=lambda r: r["repeat"]):
        cells.setdefault(r["task"], {}).setdefault(r["setup"], []).append(r)
    rows = []
    for task in sorted(cells):
        row = [Text(task)]
        for s in setups:
            t = Text()
            for r in cells[task].get(s, []):
                if r["status"] != "done":
                    t.append("run  ", style=WARN)
                elif r["reward"] == 1:
                    t.append("✓    ", style=f"bold {OK}")
                else:
                    t.append(("-" if r["tests"] is None else f"{r['tests']:.0%}").ljust(5), style=MUTED)
            row.append(t)
        rows.append(tuple(row))
    return ["Task", *setups], rows


HELP = """[b]What you are looking at[/b]
Left: every run (newest first). Type in the box to filter, e.g. [b]ho4 glmreview26[/b] or [b]running[/b].
Overview: the run as a tree: plan, each step, who built it, the senior review and what it sent back.
Who did what: one line per action, by role (PLANNER, ROUTER, WORKER, REVIEWER, ...).
Steps / Worker turns: the routing table and the worker's own thoughts and commands.
Details / Raw events / Harbor log: tokens and spend, the raw trace, Harbor's own output.

[b]Keys[/b]
n  start a run: pick a task and an evaluation setup (it fills in the config, policy and limits)
c  compare: results of an evaluation (solved, tests passed, tokens, cost) and how far it is
/  jump to the filter    s  stop the selected run    r  refresh    ?  this help    q  quit
Ctrl+P  command palette, including \"Change theme\" (default: mlr570, the same colours as the deck and report)

[b]Words[/b]
Solved = all of the task's hidden tests pass (Harbor reward 1). Tests passed = share of them that pass.
Cost = tokens x the API list price; nothing is billed per token (subscriptions)."""



def _k(n) -> str:
    n = float(n or 0)
    return f"{n / 1000:.1f}k" if n >= 1000 else f"{n:.0f}"


def _short(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _secs(ms) -> str:
    return f"{float(ms or 0) / 1000:.1f}s"


def _usage(e: dict) -> str:
    parts = [f"{_k(e.get('input_tokens'))} in", f"{_k(e.get('output_tokens'))} out"]
    if e.get("factory_credits"):
        parts.append(f"{_k(e['factory_credits'])} credits")
    if e.get("cost_usd"):
        parts.append(f"${e['cost_usd']:.4f}")
    return " · ".join(parts)


def _router_name(e: dict) -> str:
    return e.get("router") or LEGACY_ROUTER.get(str(e.get("source", "")).split(" ")[0], e.get("source", "?"))


def _tier_model(e: dict, events: list[dict]) -> str:
    """The model a route went to; older traces only logged the tier, so map it through the run's config."""
    if e.get("model"):
        return e["model"]
    tiers = next((c.get("tiers") for c in events if c.get("event") == "config"), None) or []
    tier = e.get("tier")
    return tiers[tier] if isinstance(tier, int) and tier < len(tiers) else f"tier {tier}"


def _line(t, role: str, who: str, what: str) -> Text:
    line = Text(f"{float(t or 0):7.1f}s ", style="dim")
    line.append(f"{role:<9}", style=ROLE_STYLE.get(role, ""))
    line.append(f"{who:<34} ", style="bold")
    line.append(what)
    return line


def activity_lines(events: list[dict]) -> list[Text]:
    """One line per thing that happened, naming the model or policy that did it."""
    out = []
    cfg = next((e for e in events if e.get("event") == "config"), {})
    for e in events:
        ev, t = e.get("event"), e.get("t")
        if ev == "llm_call" and e.get("role") == "planner":
            out.append(_line(t, "PLANNER", e.get("model", "?"), f"{_secs(e.get('ms'))}  {_usage(e)}"))
        elif ev == "plan":
            steps = e.get("steps", [])
            who = e.get("planner_model") or cfg.get("planner_model", "planner")
            out.append(_line(t, "PLANNER", who, f"plan: {len(steps)} steps"
                             + (" (fallback single step)" if e.get("fallback") else "")))
            for s in steps:
                out.append(Text(f"{'':17}{s['id']}: {_short(s['goal'], 95)}", style="dim"))
        elif ev == "route":
            conf = f" conf {e['confidence']:.2f}" if e.get("confidence") is not None else ""
            score = f" score {e['score']:.2f}" if e.get("score") is not None else ""
            out.append(_line(t, "ROUTER", _router_name(e)[:34],
                             f"{e.get('step')} → tier {e.get('tier')} {_tier_model(e, events)}{score}{conf} "
                             f"({float(e.get('ms') or 0):.0f} ms)"))
        elif ev == "llm_call" and e.get("role") in ("worker", "helper"):
            m = CMD_RE.search(e.get("reply", ""))
            said = f"$ {m.group(1).strip()}" if m else e.get("reply", "")
            role = "HELPER" if e.get("role") == "helper" else "LEAD" if cfg.get("helper_models") else "WORKER"
            out.append(_line(t, role, e.get("model", "?"),
                             f"{e.get('step')}  {_secs(e.get('ms'))}  {_usage(e)}  {said[:140]}"))
        elif ev == "delegate":
            out.append(_line(t, "LEAD", e.get("step", "?"), f"→ {e.get('helper')} ({e.get('to') or 'router'}: "
                             f"{e.get('model')}): {_short(e.get('task'), 110)}"))
        elif ev == "senior_fix":
            out.append(_line(t, "REVIEWER", e.get("model", "?"), f"{e.get('step')}  " + (
                f"fixing {len(e.get('problems') or [])} problem(s) still open" if e.get("phase") == "start"
                else f"fix ended: {e.get('exit_status')} after {e.get('n_calls')} calls")))
        elif ev == "senior_review":
            probs = e.get("problems") or []
            v = e.get("verdict")
            what = (f"sent it back: {len(probs)} problem(s): " + "; ".join(_short(p.get("what"), 60) for p in probs[:2])
                    if v == "fix" else f"second look: {len(probs)} still open" if v == "open"
                    else "second look: all fixed" if v == "fixed" else "ship it" if v else f"review failed: {e.get('error')}")
            out.append(_line(t, "REVIEWER", e.get("model", "?"), f"{e.get('step')}  read the diff "
                             f"({e.get('diff_chars', 0)} chars), ran {e.get('explored', 0)} command(s) → {what}"))
        elif ev == "helper_end":
            out.append(_line(t, "HELPER", e.get("helper", "?"), f"{e.get('exit_status')} after {e.get('n_calls')} "
                             f"calls: {_short(e.get('summary'), 120)}"))
        elif ev == "llm_call_failed":
            what = {"timeout": "no reply", "provider_unavailable": "provider unreachable",
                    "transient": "quick failure", "error": "failed"}.get(e.get("kind"), e.get("kind"))
            then = "retrying" if e.get("will_retry") else "giving up on this call"
            line = _line(t, e.get("role", "?").upper(), e.get("model", "?"),
                         f"{e.get('step', '')}  {_secs(e.get('ms'))}  ")
            line.append(f"CALL FAILED: {what}, {then}", style="bold #e07a68")
            out.append(line)
        elif ev == "attempt":
            check = e.get("check") or ""
            how = f"check `{check[:60]}`" if check else "no check, trusts the worker's submit"
            verdict = Text("PASS", style="bold #7fb685") if e.get("passed") else Text("FAIL", style="bold #e07a68")
            line = _line(t, "VERIFIER", "task container",
                         f"{e.get('step')} attempt {e.get('attempt')} by {e.get('model')}: {how} → ")
            line.append(verdict)
            line.append(f"  ({e.get('n_calls')} worker calls, {e.get('exit_status')})", style="dim")
            out.append(line)
        elif ev == "escalate":
            out.append(_line(t, "ESCALATE", "orchestrator",
                             f"{e.get('step')} tier {e.get('from_tier')} → {e.get('to_tier')} (counted as a router error)"))
        elif ev == "done":
            out.append(_line(t, "DONE", "orchestrator", f"escalations {e.get('n_escalations')}, "
                             f"failed steps {e.get('n_failed_steps')}"))
    return out


def roles_text(events: list[dict]) -> Text:
    """Who held each role in this run, with what they did."""
    cfg = next((e for e in events if e.get("event") == "config"), {})
    calls = [e for e in events if e.get("event") == "llm_call"]
    routes = [e for e in events if e.get("event") == "route"]
    attempts = [e for e in events if e.get("event") == "attempt"]
    text = Text()

    def row(role, body):
        text.append(f"{role:<9}", style=ROLE_STYLE.get(role, "bold"))
        text.append(body + "\n")

    planner = [c for c in calls if c.get("role") == "planner"]
    pm = (planner[0]["model"] if planner else
          next((e.get("planner_model") for e in events if e.get("event") == "plan" and e.get("planner_model")),
               cfg.get("planner_model", "?")))
    row("PLANNER", f"{pm}" + (f"  {len(planner)} call(s), {_secs(sum(c.get('ms', 0) for c in planner))}" if planner else ""))
    routers: dict[str, int] = {}
    for r in routes:
        routers[_router_name(r)] = routers.get(_router_name(r), 0) + 1
    policy = f"policy {cfg.get('policy', '?')}"
    if cfg.get("policy") == "cascade":
        policy += f", Arch-Router below conf {cfg.get('confidence_threshold')}"
    row("ROUTER", (", ".join(f"{k} ×{v}" for k, v in routers.items()) or "(no step routed yet)") + f"  [{policy}]")
    by_model: dict[str, list] = {}
    for c in calls:
        if c.get("role") == "worker":
            by_model.setdefault(c["model"], []).append(c)
    if by_model:
        row("WORKER", "; ".join(
            f"{m} ×{len(cs)} ({_secs(sum(c.get('ms', 0) for c in cs))}, "
            f"{_usage({k: sum(c.get(k) or 0 for c in cs) for k in ('input_tokens', 'output_tokens', 'factory_credits', 'cost_usd')})})"
            for m, cs in by_model.items()))
    else:
        used = {a.get("model") for a in attempts}
        row("WORKER", ", ".join(sorted(used)) or "(no worker call yet)")
    passed = sum(1 for a in attempts if a.get("passed"))
    row("VERIFIER", f"step checks in the task container: {passed} pass, {len(attempts) - passed} fail")
    tiers = cfg.get("tiers") or []
    used_models = {r.get("model") for r in routes} | {a.get("model") for a in attempts}
    if tiers:
        text.append("TIERS    ", style="bold")
        for i, m in enumerate(tiers):
            style = "bold" if m in used_models else "dim"
            text.append(f"{i}:{m}" + (" (escalation)" if i == len(tiers) - 1 else ""), style=style)
            text.append("   ")
    return text


def totals(events: list[dict]) -> dict:
    calls = [e for e in events if e.get("event") == "llm_call"]
    keys = ("input_tokens", "output_tokens", "factory_credits", "cost_usd", "ms")
    return {"calls": len(calls), **{k: sum(c.get(k) or 0 for c in calls) for k in keys}}


def step_rows(events: list[dict]) -> list[tuple]:
    plan = next((e for e in events if e.get("event") == "plan"), None)
    if not plan:
        return []
    rows = []
    for s in plan.get("steps", []):
        route = next((e for e in events if e.get("event") == "route" and e.get("step") == s["id"]), None)
        atts = [e for e in events if e.get("event") == "attempt" and e.get("step") == s["id"]]
        if atts and atts[-1].get("passed"):
            status = "PASS"
        elif atts and (len(atts) == 2 or not any(e.get("event") == "escalate" and e.get("step") == s["id"]
                                                 for e in events)):
            status = "FAIL"
        elif route:
            status = "running"
        else:
            status = "waiting"
        calls = sum(1 for e in events if e.get("event") == "llm_call" and e.get("step") == s["id"])
        rows.append((s["id"], _short(s["goal"], 38), _router_name(route).split(" (")[0][:16] if route else "-",
                     _tier_model(route, events) if route else "-",
                     " → ".join(a.get("model", "?").split("/")[-1] for a in atts) or "-", status,
                     str(calls or sum(a.get("n_calls") or 0 for a in atts))))
    return rows


def worker_lines(messages: list[dict], max_obs: int = 1500) -> list[Text]:
    out = []
    for m in messages[2:]:  # skip system + task prompt
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "assistant":
            out.append(Text("model ▸ ", style="bold #8fb0d9") + Text(content.strip()))
        elif role == "exit":
            out.append(Text(f"exit ▸ {m.get('extra', {}).get('exit_status', '')} {content[:300]}", style="bold"))
        else:
            out.append(Text("shell ◂ ", style="bold #7fb685") + Text(content.strip()[:max_obs], style="dim"))
        out.append(Text(""))
    return out


# ---------- plain-language story (the default "Who did what" view) ----------

PRETTY = {"gpt": "GPT", "glm": "GLM", "deepseek": "DeepSeek", "kimi": "Kimi", "claude": "Claude",
          "sol": "Sol", "flash": "Flash", "haiku": "Haiku", "sonnet": "Sonnet", "opus": "Opus"}
BACKEND = {"droid": "via Droid", "claude": "via Claude Code"}
SUBMIT_MARK = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
OBS_RE = re.compile(r"<returncode>(-?\d+)</returncode>\s*(?:<output>\s*(.*?)\s*(?:</output>|$))?", re.S)


def pretty_model(model_id: str) -> str:
    """droid/deepseek-v4.1-flash -> 'DeepSeek V4.1 Flash (via Droid)'."""
    backend, _, name = model_id.partition("/") if "/" in model_id else ("", "", model_id)
    name = name.removeprefix("claude-") if backend == "claude" else name
    words = []
    for w in re.split(r"[-_]", name):
        if re.fullmatch(r"v?\d+(\.\d+)*", w):
            words.append(w.upper() if w.startswith("v") else w)
        else:
            words.append(PRETTY.get(w, w.capitalize()))
    text = " ".join(words)
    text = re.sub(r"(\d) (\d)", r"\1.\2", text)  # claude-opus-5-5 -> Opus 5.5
    text = re.sub(r"\bGPT (\d)", r"GPT-\1", text)
    if backend == "claude":
        text = "Claude " + text
    return f"{text} ({BACKEND.get(backend, backend)})" if backend else text


def difficulty(score) -> str:
    if score is None:
        return ""
    return "easy" if score < 0.67 else "medium" if score < 1.33 else "hard"


def describe_command(cmd: str) -> str:
    """A few words on what a shell command does, for people skimming the run."""
    if SUBMIT_MARK in cmd:
        return "declared the step done"
    first_line = cmd.strip().splitlines()[0] if cmd.strip() else ""
    first_line = re.sub(r"\d?>>?\s*&\d|\d?>>?\s*/dev/null", "", first_line)  # 2>&1, >/dev/null: not files
    parts = [p.strip() for p in first_line.split("&&")]
    core = next((p for p in parts if not p.startswith("cd ")), parts[-1])  # first real action after any cd
    first = core.split("|")[0].strip()
    tok = first.split()
    verb = tok[0] if tok else ""
    args = [t for t in tok[1:] if not t.startswith("-")]
    target = " ".join(args[:3])
    table = {
        "ls": "listed files", "tree": "listed files", "find": "searched for files",
        "cat": f"read {target}", "head": f"read the start of {target}", "tail": f"read the end of {target}",
        "less": f"read {target}", "wc": f"counted lines in {target}", "grep": "searched text", "rg": "searched text",
        "awk": f"extracted columns from {args[-1] if args else 'data'}", "sort": "sorted data", "uniq": "counted repeats",
        "xxd": f"hex-dumped {target}", "od": f"hex-dumped {args[-1] if args else ''}", "hexdump": f"hex-dumped {target}",
        "file": f"checked file types of {target}", "strings": f"pulled text out of {target}",
        "python3": "ran a Python script", "python": "ran a Python script", "node": "ran a JS script",
        "pip": "installed Python packages", "pip3": "installed Python packages", "apt-get": "installed system packages",
        "mkdir": "made a folder", "cp": "copied files", "mv": "moved files", "rm": "deleted files",
        "echo": "wrote output", "printf": "wrote output", "sed": "edited text", "git": f"used git ({target})",
        "curl": "fetched a URL", "wget": "fetched a URL", "make": "ran make", "gcc": "compiled C", "cargo": "ran cargo",
        "npm": "ran npm", "pytest": "ran tests", "jq": "queried JSON",
        "for": "looped over items", "while": "ran a loop", "bash": "ran a shell script", "sh": "ran a shell script",
        "test": "checked a condition", "diff": "compared files", "tar": "unpacked an archive", "unzip": "unpacked an archive",
        "delegate": "handed sub-task(s) to helpers",
    }
    desc = table.get(verb, f"ran {verb}" if verb else "ran a command")
    m = re.search(r">>?\s*([\w./~-]+)", core)
    if m and verb in ("echo", "printf", "cat", "tee"):
        desc = f"wrote {m.group(1)}"
    elif verb in ("python3", "python") and "<<" in core:
        desc = "ran a Python script"
    return desc


def split_reply(text: str) -> tuple[str, str]:
    """(what the model says it is doing, the shell command) from one worker reply."""
    m = CMD_RE.search(text or "")
    cmd = m.group(1).strip() if m else ""
    thought = (text or "")[: m.start()] if m else (text or "")
    thought = " ".join(thought.replace("THOUGHT:", "").split())
    return thought, cmd


def summarize_output(obs: str) -> tuple[bool, str]:
    m = OBS_RE.search(obs or "")
    if not m:
        return True, _short(obs, 110)
    code, body = int(m.group(1)), (m.group(2) or "")
    lines = [l for l in body.splitlines() if l.strip() and not re.match(r"total \d+$", l.strip())
             and not l.startswith("[Clock]")]  # v2.6's clock line is not the command's output
    first = _short(lines[0], 100) if lines else "(no output)"
    trimmed = " · long output, trimmed" if "characters elided" in body else ""
    head = f"ok · {len(lines)} lines{trimmed}" if code == 0 else f"failed (exit {code})"
    return code == 0, f"{head} · {first}"


def clock_nudge(obs: str) -> str:
    """v2.6: the clock's wrap-up or submit-now line in a command's output, shortened ("" if none)."""
    m = re.search(r"\[Clock\] ([^\n]*?) used\. (Wrap up|Submit now)", obs)
    return f"{m.group(1)} used: told to {m.group(2).lower()}" if m else ""


def _attempt_files(workers: dict, step: str) -> list[tuple[int, list]]:
    pat = re.compile(rf"worker_{re.escape(step)}_a(\d+)$")  # not repair turns or a lead's helpers
    return sorted(((int(m.group(1)), v) for k, v in workers.items() if (m := pat.match(k))), key=lambda kv: kv[0])


def story_lines(events: list[dict], workers: dict[str, list]) -> list[Text]:
    """What each agent did, in words, grouped by step. `workers` maps 'worker_s1_a1' -> messages."""
    out: list[Text] = []
    cfg = next((e for e in events if e.get("event") == "config"), {})
    tiers = cfg.get("tiers") or []
    plan = next((e for e in events if e.get("event") == "plan"), None)
    pcalls = [e for e in events if e.get("event") == "llm_call" and e.get("role") == "planner"]

    def head(role: str, who: str, extra: str = "") -> Text:
        t = Text(f"{role} ", style=ROLE_STYLE.get(role, "bold"))
        t.append(who, style="bold")
        if extra:
            t.append(f"  {extra}", style="dim")
        return t

    if plan and plan.get("planner") == "skipped":
        out.append(head("PLANNER", "none", "one agent does the whole task"))
        out.append(Text(""))
    elif plan:
        who = pretty_model(plan.get("planner_model") or cfg.get("planner_model", "planner"))
        took = f"{sum(c.get('ms', 0) for c in pcalls) / 1000:.0f}s" if pcalls else ""
        out.append(head("PLANNER", who, took))
        steps = plan.get("steps", [])
        lead = "Could not plan, so it treats the whole task as one step:" if plan.get("fallback") else \
            f"Split the task into {len(steps)} step{'s' if len(steps) != 1 else ''}:"
        out.append(Text(f"  {lead}"))
        for s in steps:
            after = f" (after {', '.join(s['depends_on'])})" if s.get("depends_on") else ""
            out.append(Text(f"    {s['id']}{after}: {_short(s['goal'], 150)}"))
            out.append(Text(f"        done when: {_short(s['check'], 90) if s.get('check') else 'the worker says it is done'}",
                            style="dim"))
        out.append(Text(""))
    elif cfg:
        out.append(head("PLANNER", pretty_model(cfg.get("planner_model", "?")), "planning ..."))

    for s in (plan or {}).get("steps", []):
        sid = s["id"]
        route = next((e for e in events if e.get("event") == "route" and e.get("step") == sid), None)
        if not route:
            out.append(Text(f"STEP {sid}  waiting for: {', '.join(s.get('depends_on') or []) or 'its turn'}",
                            style="dim"))
            continue
        out.append(Text(f"STEP {sid}  {_short(s['goal'], 120)}", style="bold underline"))
        model = _tier_model(route, events)
        router = _router_name(route).split(" (")[0]
        if route.get("score") is not None:
            sure = f", {route['confidence'] * 100:.0f}% sure" if route.get("confidence") is not None else ""
            judged = f"judged it {difficulty(route['score'])} ({route['score']:.2f} of 2{sure})"
        else:
            judged = f"used {_router_name(route)}"
        t = head("ROUTER", router)
        t.append(f"  {judged} → sent it to ")
        t.append(pretty_model(model), style="bold")
        t.append(f" (tier {route.get('tier')} of {len(tiers) - 1})" if tiers else "", style="dim")
        out.append(t)
        calls = [e for e in events if e.get("event") == "llm_call" and e.get("role") == "worker" and e.get("step") == sid]
        call_i = 0
        attempts = {e.get("attempt"): e for e in events if e.get("event") == "attempt" and e.get("step") == sid}
        esc = next((e for e in events if e.get("event") == "escalate" and e.get("step") == sid), None)
        esc_model = tiers[esc["to_tier"]] if esc and tiers and esc.get("to_tier", 99) < len(tiers) else ""
        for n, msgs in _attempt_files(workers, sid):
            if n > 1 and esc:
                out.append(head("ESCALATE", "orchestrator", f"first attempt did not pass, retrying one tier up on "
                                f"{pretty_model(esc_model) if esc_model else 'the next tier'}"))
            amodel = attempts.get(n, {}).get("model") or (model if n == 1 else esc_model)
            out.append(head("LEAD" if cfg.get("helper_models") else "WORKER", pretty_model(amodel) if amodel else "worker",
                            f"attempt {n}, tool: bash in the task container"
                            + (", can hand sub-tasks to helpers" if cfg.get("helper_models") else "")))
            turn = 0
            for i, m in enumerate(msgs[2:], start=2):
                if m.get("role") != "assistant":
                    continue
                turn += 1
                thought, cmd = split_reply(str(m.get("content") or ""))
                secs = f"{calls[call_i]['ms'] / 1000:.0f}s" if call_i < len(calls) else ""
                call_i += 1
                line = Text(f"  {turn:>2}. ", style="dim")
                line.append(describe_command(cmd) if cmd else "replied without a command", style="bold")
                if secs:
                    line.append(f"  ({secs})", style="dim")
                out.append(line)
                if thought:
                    out.append(Text(f"      thinking: {_short(thought, 170)}", style="italic"))
                if cmd and SUBMIT_MARK not in cmd:
                    out.append(Text(f"      $ {_short(cmd, 170)}", style="#8fb0d9"))
                nxt = msgs[i + 1] if i + 1 < len(msgs) else None
                if nxt and nxt.get("role") == "exit":
                    out.append(Text(f"      → {_short(nxt.get('content'), 170)}", style="#7fb685"))
                elif nxt and nxt.get("role") != "assistant":
                    ok, summary = summarize_output(str(nxt.get("content") or ""))
                    out.append(Text(f"      → {summary}", style="#7fb685" if ok else "#e07a68"))
                    if nudge := clock_nudge(str(nxt.get("content") or "")):
                        out.append(Text(f"      CLOCK {nudge}", style="bold #e2b55c"))
                elif not nxt:
                    out.append(Text("      … running", style="#e2b55c"))
            a = attempts.get(n)
            if a:
                v = head("VERIFIER", "task container")
                how = f"ran `{_short(a['check'], 60)}`" if a.get("check") else "no check for this step, accepted the worker's done"
                v.append(f"  {how} → ")
                v.append("passed ✔" if a.get("passed") else "failed ✘", style="bold #7fb685" if a.get("passed") else "bold #e07a68")
                if a.get("exit_status") not in (None, "Submitted"):
                    v.append(f"  (worker stopped: {a['exit_status']})", style="dim")
                out.append(v)
        for r in (e for e in events if e.get("event") == "senior_review" and e.get("step") == sid):
            probs = r.get("problems") or []
            out.append(head("REVIEWER", pretty_model(r.get("model", "")), "read the task, the transcript and the diff"))
            v = r.get("verdict")
            out.append(Text("      → ship it" if v == "ship" else "      → second look: all fixed" if v == "fixed"
                            else f"      → second look: {len(probs)} still open" if v == "open"
                            else f"      → sent back {len(probs)} problem(s)",
                            style="#7fb685" if v in ("ship", "fixed") else "#e2b55c"))
            for p in probs:
                out.append(Text(f"        • {_short(p.get('what'), 160)}", style="#e2b55c"))
        for h in helper_states(events, sid):
            out.append(head("HELPER", pretty_model(h["model"]), f"{h['id']}, given a sub-task by the lead · "
                            f"{h['calls']} calls · {spend_text(h['spend'])}"))
            out.append(Text(f"      task: {_short(h['task'], 170)}"))
            out.append(Text(f"      → {_short(h['summary'], 170)}" if h["summary"] else "      … working",
                            style={"done": "#7fb685", "failed": "#e07a68"}.get(h["state"], "#e2b55c")))
        if esc and not _attempt_files(workers, sid)[1:]:
            out.append(head("ESCALATE", "orchestrator", f"retrying one tier up on {pretty_model(esc_model) or 'the next tier'}"))
        out.append(Text(""))
    done = next((e for e in events if e.get("event") == "done"), None)
    if done:
        ok = done.get("n_failed_steps") == 0
        out.append(head("DONE", "orchestrator",
                        "all steps passed" if ok else f"{done.get('n_failed_steps')} step(s) failed"
                        + (f", {done.get('n_escalations')} escalation(s)" if done.get("n_escalations") else "")))
    return out

# ---------- Overview tab: a plain tree of what happened, no numbers ----------

def first_sentence(text: str, n: int = 100) -> str:
    text = " ".join(re.sub(r"</?(thought|thinking)>", " ", text or "").split())  # GLM wraps its thoughts in tags
    if not text:
        return ""
    guarded = re.sub(r"\b(e\.g|i\.e|etc|vs)\.", lambda m: m.group(0).replace(".", "\0"), text)
    cut = re.split(r"(?<=[.!?])\s", guarded, maxsplit=1)[0].rstrip(".").replace("\0", ".")
    return _short(cut, n)


def turn_summary(thought: str, cmd: str, ok: bool | None) -> Text:
    if cmd and SUBMIT_MARK in cmd:
        return Text("Said the step is finished", style="bold")
    action = describe_command(cmd) if cmd else "answered without running anything"
    action = action[0].upper() + action[1:]
    t = Text()
    t.append(action, style="bold")
    why = first_sentence(thought)
    if why:
        t.append(f" — {why}", style=MUTED)
    if ok is False:
        t.append("  ✗ failed", style=BAD)
    elif ok is None:
        t.append("  … working", style="#e2b55c")
    return t


def _clock(sec: float) -> str:
    sec = max(int(sec), 0)
    return f"{sec // 3600}h {sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m {sec % 60:02d}s"


def step_states(events: list[dict], now_t: float) -> list[dict]:
    """Plain status of every planned step: waiting / working / retrying / done / failed, with its
    model, actions so far and timings. `now_t` is the current time on the trace clock (seconds)."""
    plan = next((e for e in events if e.get("event") == "plan"), None)
    if not plan:
        return []
    steps = plan["steps"]
    merged = next((e for e in events if e.get("event") == "merged"), None)
    if merged:  # a pure chain ran as one step
        steps = [{"id": merged["into"], "depends_on": [],
                  "goal": f"all {len(merged['parts'])} planned parts as one step: " + steps[0]["goal"]}]
    out = []
    for s in steps:
        sid = s["id"]
        mine = [e for e in events if e.get("step") == sid]
        route = next((e for e in mine if e.get("event") == "route"), None)
        attempts = [e for e in mine if e.get("event") == "attempt"]
        calls = [e for e in mine if e.get("event") == "llm_call"]
        escalations = [e for e in mine if e.get("event") == "escalate"]
        failures = [e for e in mine if e.get("event") == "llm_call_failed"]
        end = next((e for e in mine if e.get("event") == "step_end"), None)
        done = end["passed"] if end else any(a.get("passed") for a in attempts)
        over = bool(end) or done or (attempts and len(attempts) > len(escalations))
        state = ("done" if done else "failed" if over else "retrying" if escalations
                 else "working" if route else "waiting")
        model = (route or {}).get("model") or (attempts[-1].get("model") if attempts else None)
        if escalations:
            tiers = next((e.get("tiers") for e in events if e.get("event") == "config"), None) or []
            to = escalations[-1].get("to_tier")
            model = tiers[to] if isinstance(to, int) and to < len(tiers) else (calls[-1]["model"] if calls else model)
        # current attempt's calls only: actions since the last escalation
        since = escalations[-1]["t"] if escalations else (route or {}).get("t", 0)
        cur_calls = [c for c in calls if c["t"] >= since]
        started = (route or {}).get("t")
        ended = attempts[-1]["t"] if over and attempts else None
        last = max([e["t"] for e in mine] or [since])
        out.append({
            "id": sid, "goal": s["goal"], "state": state, "model": model,
            "depends_on": s.get("depends_on") or [],
            "actions": len(cur_calls) or (attempts[-1].get("n_calls") or 0 if over and attempts else 0),
            "attempt": len(escalations) + 1,
            "elapsed": ((ended if ended is not None else now_t) - started) if started is not None else 0,
            "attempt_elapsed": (now_t - since) if state in ("working", "retrying") else 0,
            "idle": (now_t - last) if state in ("working", "retrying") else 0,
            "failed_calls": len(failures),
            "has_check": bool((s.get("check") or "").strip()),
        })
    return out


def helper_states(events: list[dict], step_id: str) -> list[dict]:
    """The helpers a lead step handed sub-tasks to: id, model, task, working/done/failed, calls and spend."""
    out = []
    for d in (e for e in events if e.get("event") == "delegate" and e.get("step") == step_id):
        hid = d.get("helper")
        end = next((e for e in events if e.get("event") == "helper_end" and e.get("helper") == hid), None)
        calls = [e for e in events if e.get("event") == "llm_call" and e.get("step") == hid]
        state = "working" if not end else "done" if end.get("exit_status") == "Submitted" else "failed"
        out.append({"id": hid, "model": d.get("model", ""), "task": d.get("task", ""), "state": state,
                    "calls": len(calls), "spend": spend(calls), "summary": (end or {}).get("summary", "")})
    return out


STATE_LOOK = {"done": ("✓", "bold #7fb685", "done"), "failed": ("✗", "bold #e07a68", "failed"),
              "working": ("●", "bold #e2b55c", "working"), "retrying": ("●", "bold #e2b55c",
                                                                     "retrying with a stronger model"),
              "waiting": ("○", "dim", "waiting"), "stopped": ("■", "dim", "stopped mid-work")}


def trace_now(job, events: list[dict]) -> float:
    """Seconds on the trace clock right now (trace t=0 is when the harness started). For a finished run,
    the time of its last event."""
    import time as _time

    if not events or not job.agent_dir:
        return 0.0
    trace = job.agent_dir / "trace.jsonl"
    try:
        start = trace.stat().st_mtime - events[-1]["t"]
    except OSError:
        return events[-1]["t"]
    return (_time.time() - start) if job.status == "running" else events[-1]["t"]


def spend(events: list[dict], step_id: str | None = None) -> dict:
    """Model spend from the trace: list-price USD, Factory credits, input tokens and the cached share."""
    calls = [e for e in events if e.get("event") == "llm_call" and (step_id is None or e.get("step") == step_id)]
    inp = sum(e.get("input_tokens") or 0 for e in calls)
    return {"usd": trace_usd(calls), "credits": sum(e.get("factory_credits") or 0 for e in calls),
            "input": inp, "cached": sum(e.get("cached_tokens") or 0 for e in calls), "calls": len(calls)}


def spend_text(s: dict) -> str:
    """'$0.25 (81,878 credits)': dollars at API list price, plus the Factory credits Droid metered."""
    return f"${s['usd']:.2f}" + (f" ({s['credits']:,.0f} credits)" if s["credits"] else "")


def progress_panel(events: list[dict], now_t: float, status: str = "running", reward=None) -> Text:
    """The top panel: what the run is doing right now, in plain words, with a progress bar."""
    cfg = next((e for e in events if e.get("event") == "config"), {})
    steps = step_states(events, now_t)
    if status != "running":  # a step that was mid-work when the run ended did not finish
        for st in steps:
            if st["state"] in ("working", "retrying"):
                st["state"] = "stopped"
    t = Text()
    if not events:
        t.append("Starting up: building the task's container and loading the router. This takes a minute or two.",
                 style="dim")
        return t
    if not steps:
        planner = pretty_model(cfg.get("planner_model", "the planner"))
        t.append(f"Planning: {planner} is reading the task and splitting it into steps "
                 f"({_clock(now_t)} so far).", style="#e2b55c")
        return t
    n_done = sum(s["state"] == "done" for s in steps)
    n_fail = sum(s["state"] == "failed" for s in steps)
    width = 24
    good = round(width * n_done / len(steps))
    bad = round(width * (n_done + n_fail) / len(steps)) - good
    t.append("Progress  ", style="bold")
    t.append("█" * good, style=OK)
    t.append("█" * bad, style=BAD)
    t.append("░" * (width - good - bad), style="dim")
    t.append(f"  {n_done} of {len(steps)} steps done" + (f", {n_fail} failed" if n_fail else ""))
    t.append(f"   ·   {'running for' if status == 'running' else 'ran for'} {_clock(now_t)}\n", style="dim")
    total = spend(events)
    caps = [f"${cfg['max_run_cost_usd']:.2f}" if cfg.get("max_run_cost_usd") else "",
            f"{cfg['max_run_credits']:,.0f} credits" if cfg.get("max_run_credits") else ""]
    cap = " / ".join(c for c in caps if c)
    line = f"Spent     {spend_text(total)} at list price" + (f", cap {cap}" if cap else "")
    if total["input"]:
        line += f"   ·   {100 * total['cached'] / total['input']:.0f}% of input from cache"
    t.append(line + "\n", style="dim")
    if any(e.get("event") == "delegate" for e in events):
        helpers = [e for e in events if e.get("event") == "llm_call" and e.get("role") == "helper"]
        lead = spend([e for e in events if e.get("role") != "helper"])
        hs = spend(helpers)
        t.append(f"Team      lead {spend_text(lead)} · helpers {spend_text(hs)} over {hs['calls']} calls "
                 f"({sum(1 for e in events if e.get('event') == 'delegate')} sub-tasks handed out)\n", style="dim")
    team = {k: sum(1 for e in events if e.get("event") == k) for k in ("fork", "triage", "rerun", "stop")}
    updates = sum(1 for e in events if e.get("event") == "llm_call" and e.get("team_update"))
    reviews = sum(1 for e in events if e.get("event") == "llm_call" and e.get("purpose") == "review")
    bits = [f"{updates} team update(s)" if updates else "", f"{team['fork']} step(s) continued a teammate's memory"
            if team["fork"] else "", f"{reviews} value review(s)" if reviews else "",
            f"{team['triage']} error-handler call(s)" if team["triage"] else "",
            f"{team['stop']} stop(s) to save budget" if team["stop"] else ""]
    acc = next((e for e in events if e.get("event") == "acceptance"), None)
    if any(e.get("event") == "acceptance_planned" for e in events):
        bits.append("acceptance tests " + ("being written" if not acc else "ready" if acc.get("ready") else "FAILED to write"))
    reviews_sr = [e for e in events if e.get("event") == "senior_review"]
    if reviews_sr:
        sent = sum(e.get("verdict") == "fix" for e in reviews_sr)
        bits.append(f"senior review by {pretty_model(reviews_sr[-1].get('model', ''))}: "
                    + (f"sent the work back {sent} time(s)" if sent else "approved"))
    reopens = sum(1 for e in events if e.get("event") == "reopen")
    if reopens:
        bits.append(f"{reopens} step(s) re-opened by acceptance tests")
    if any(bits):
        t.append("Team      " + " · ".join(b for b in bits if b) + "\n", style="dim")
    turn_cap = cfg.get("worker_step_limit")
    time_cap = cfg.get("step_wall_time_sec")
    idle_cap = cfg.get("step_idle_sec")
    for i, s in enumerate(steps, 1):
        icon, style, word = STATE_LOOK[s["state"]]
        t.append(f"  {icon} Step {i} ({s['id']})  ", style=style)
        t.append(_short(first_sentence(s["goal"], 70), 70))
        t.append("\n")
        who = pretty_model(s["model"]) if s["model"] else ""
        if s["state"] == "waiting":
            deps = [str(next(j for j, x in enumerate(steps, 1) if x["id"] == d)) for d in s["depends_on"]
                    if any(x["id"] == d for x in steps)]
            which = (f"step {deps[0]}" if len(deps) == 1 else
                     "steps " + ", ".join(deps[:-1]) + " and " + deps[-1]) if deps else ""
            t.append(f"           waits for {which} to finish\n" if deps else
                     "           waiting for a free worker slot\n", style="dim")
        elif s["state"] in ("working", "retrying"):
            line = f"           {word}: {who} · action {s['actions'] + 1}"
            line += f" of {turn_cap}" if turn_cap else ""
            line += f" · {_clock(s['attempt_elapsed'])}" + (f" of {_clock(time_cap)} allowed" if time_cap else "")
            t.append(line + "\n", style="#e2b55c")
            wait = Text(f"           waiting for {who.split(' (')[0]}'s next move: {_clock(s['idle'])}",
                        style="dim")
            if idle_cap and s["idle"] > idle_cap * 0.5:
                wait.stylize("bold #e07a68")
                wait.append(f"  (gives up at {_clock(idle_cap)} with no reply)", style="#e07a68")
            t.append_text(wait)
            if s["failed_calls"]:
                t.append(f"  · {s['failed_calls']} failed model call(s)", style="#e07a68")
            t.append("\n")
        elif s["state"] == "stopped":
            t.append(f"           {word}: {who} had taken {s['actions']} actions\n", style="dim")
        else:
            line = (f"           {word} by {who} in {_clock(s['elapsed'])} · {s['actions']} actions"
                    f" · {spend_text(spend(events, s['id']))}")
            if s["attempt"] > 1:
                line += f" · needed {s['attempt'] - 1} escalation(s)"
            t.append(line + "\n", style="dim")
        for h in helper_states(events, s["id"]):
            look = {"working": ("●", "#e2b55c"), "done": ("✓", "#7fb685"), "failed": ("✗", "#e07a68")}[h["state"]]
            t.append(f"           ↳ {look[0]} helper {h['id']} ", style=look[1])
            t.append(pretty_model(h["model"]), style="bold")
            t.append(f"  {_short(h['task'], 60)}  ·  {h['calls']} calls · {spend_text(h['spend'])}\n", style="dim")
    if status != "running":
        if reward is not None:
            verdict = "passed" if reward == 1 else "did not pass"
            t.append(f"\nTerminal-Bench result: {verdict} (score {reward})", style="bold")
            if reward == 1 and n_fail:
                t.append(f"\n(The task passed although {n_fail} step(s) failed their own check: the planner's "
                         "check was stricter than, or different from, the task's tests.)", style="dim")
        else:
            t.append(f"\nRun ended: {status}", style="bold")
    return t


def overview(events: list[dict], workers: dict[str, list], result: dict | None = None) -> list[tuple]:
    """Tree rows as (key, label, children); children has the same shape. Pure, for the Tree widget and tests."""
    cfg = next((e for e in events if e.get("event") == "config"), {})
    tiers = cfg.get("tiers") or []
    plan = next((e for e in events if e.get("event") == "plan"), None)
    rows = []
    if not plan:
        who = pretty_model(cfg.get("planner_model", "")) if cfg else "the planner"
        rows.append(("plan", Text(f"Planner: {who} is reading the task and making a plan …", style="#e2b55c"), []))
        return rows
    steps = plan.get("steps", [])
    planner = pretty_model(plan.get("planner_model") or cfg.get("planner_model", "?"))
    if plan.get("planner") == "skipped":  # single-agent setups (baselines, build-then-review): no planner call
        rows.append(("plan", Text.assemble(("Plan: ", "bold #b6a0e0"), "no planner; one agent does the whole task"), []))
    else:
        rows.append(("plan", Text.assemble(("Planner: ", "bold #b6a0e0"), (planner, "bold"),
                                           f" split the task into {len(steps)} step{'s' if len(steps) != 1 else ''}"), []))
    rows += strategy_rows(events)
    for s in steps:
        sid = s["id"]
        route = next((e for e in events if e.get("event") == "route" and e.get("step") == sid), None)
        atts = {e.get("attempt"): e for e in events if e.get("event") == "attempt" and e.get("step") == sid}
        last = atts[max(atts)] if atts else None
        esc = next((e for e in events if e.get("event") == "escalate" and e.get("step") == sid), None)
        finished = last and (last.get("passed") or not esc or len(atts) == 2)
        if last and last.get("passed"):
            icon, style = "✔", "bold #7fb685"
        elif finished:
            icon, style = "✘", "bold #e07a68"
        elif route:
            icon, style = "●", "bold #e2b55c"
        else:
            icon, style = "○", "dim"
        label = Text()  # Text(style=...) would colour the whole label, not just the icon
        label.append(f"{icon} ", style=style)
        label.append(_short(s["goal"], 95), style="bold" if route else "dim")
        children = []
        if not route:
            wait = ", ".join(s.get("depends_on") or [])
            children.append((f"{sid}/wait", Text(f"Waiting for {wait or 'its turn'}", style="dim"), []))
            rows.append((sid, label, children))
            continue
        router = _router_name(route).split(" (")[0]
        target = pretty_model(_tier_model(route, events))
        rated = f" rated it {difficulty(route['score'])}" if route.get("score") is not None else ""
        children.append((f"{sid}/route", Text.assemble(("Router: ", "bold #8fb0d9"), (router, "bold"),
                                                       f"{rated} and gave it to ", (target, "bold")), []))
        esc_model = tiers[esc["to_tier"]] if esc and tiers and esc.get("to_tier", 99) < len(tiers) else ""
        handoff = next((e for e in events if e.get("event") == "handoff" and e.get("step") == sid), None)
        helper_names, helpers = cfg.get("helper_models") or {}, helper_states(events, sid)
        reviews = [e for e in events if e.get("event") == "senior_review" and e.get("step") == sid]
        for n, msgs in _attempt_files(workers, sid):
            if n > 1 and esc:
                children.append((f"{sid}/esc", Text.assemble(
                    ("Escalated: ", "bold #e2b55c"), f"the first attempt did not pass, so the step goes to ",
                    (pretty_model(esc_model) if esc_model else "the next tier", "bold"),
                    " with the first attempt's notes" if handoff else " (starting over)"), []))
            model = pretty_model((atts.get(n) or {}).get("model") or
                                 (_tier_model(route, events) if n == 1 else esc_model) or "?")
            turns = []
            assistant_idx = [i for i, m in enumerate(msgs) if i >= 2 and m.get("role") == "assistant"]
            for k, i in enumerate(assistant_idx):
                thought, cmd = split_reply(str(msgs[i].get("content") or ""))
                nxt = msgs[i + 1] if i + 1 < len(msgs) else None
                ok = None if nxt is None else (True if nxt.get("role") == "exit" else summarize_output(str(nxt.get("content") or ""))[0])
                handed = parse_delegate(cmd or "", list(helper_names)) if helper_names else None
                kids = [helper_node(f"{sid}/a{n}/t{k}", helpers.pop(0), workers) for _ in (handed or []) if helpers]
                turns.append((f"{sid}/a{n}/t{k}", turn_summary(thought, cmd, ok), kids))
                if reviews and nxt is not None and "senior reviewer" in str(nxt.get("content") or ""):
                    turns.append(review_node(f"{sid}/a{n}/r{k}", reviews.pop(0)))  # the review sent this submit back
            head = Text.assemble(("Lead: " if helper_names else "Worker: ", "bold #8fb0d9"), (model, "bold"),
                                 " (second try, one tier up)" if n > 1 else "")
            children.append((f"{sid}/a{n}", head, turns or [(f"{sid}/a{n}/start", Text("Starting …", style="#e2b55c"), [])]))
            a = atts.get(n)
            if a:
                if a.get("check"):
                    verdict = "passed" if a.get("passed") else "failed"
                    text = Text.assemble(("Check: ", "bold #7fb685"), f"the step's test {verdict}")
                else:
                    text = Text.assemble(("Check: ", "bold #7fb685"),
                                         "no test for this step, so the worker's word was accepted"
                                         if a.get("passed") else "no test, and the worker did not finish")
                if a.get("exit_status") not in (None, "Submitted"):
                    text.append(f" (worker stopped: {EXIT_REASON.get(a['exit_status'], a['exit_status'])})", style="dim")
                children.append((f"{sid}/a{n}/check", text, []))
        children += [helper_node(sid, h, workers) for h in helpers]  # handed out but not yet in the lead's file
        children += [review_node(f"{sid}/rv{i}", r) for i, r in enumerate(reviews)]  # second looks
        for i, f in enumerate(e for e in events if e.get("event") == "senior_fix" and e.get("step") == sid
                              and e.get("phase") == "end"):
            children.append((f"{sid}/fix{i}", Text.assemble(("Senior fix: ", "bold #b6a0e0"), (pretty_model(f.get("model", "")), "bold"),
                             f" fixed what was still open ({f.get('n_calls')} calls, {f.get('exit_status')})"), []))
        if esc and not any(k == f"{sid}/esc" for k, _, _ in children):
            children.append((f"{sid}/esc", Text.assemble(("Escalated: ", "bold #e2b55c"), "to ",
                                                         (pretty_model(esc_model) or "the next tier", "bold")), []))
        rows.append((sid, label, children))
    done = next((e for e in events if e.get("event") == "done"), None)
    if result is not None:
        reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if reward is not None:
            rows.append(("result", Text.assemble(("Result: ", "bold"),
                                                 ("task solved ✔" if reward == 1 else "task not solved ✘",
                                                  "bold #7fb685" if reward == 1 else "bold #e07a68"),
                                                 " (Terminal-Bench's own tests)"), []))
    elif done:
        rows.append(("done", Text("All steps finished; Terminal-Bench is grading the result …", style="#e2b55c"), []))
    return rows

def strategy_rows(events: list[dict]) -> list[tuple]:
    """v2.7: how the run decided to work (phase2.strategy), with the brief it gave the worker."""
    from .strategy import MODES

    out = []
    for e in (e for e in events if e.get("event") == "strategy"):
        head = Text()
        head.append("Way of working: ", style=f"bold {ACCENT}")
        head.append(MODES.get(e.get("mode"), (e.get("mode"), ""))[0], style="bold")
        if e.get("why"):
            head.append(f" — {_short(e['why'], 90)}", style=MUTED)
        kids = [(f"strategy/{e.get('step')}/{k}/{i}", Text(f"{label}: {_short(x, 110)}", style=MUTED), [])
                for k, label in (("deliverables", "must exist"), ("targets", "target"), ("risks", "risk"))
                for i, x in enumerate(e.get(k) or [])]
        out.append((f"strategy/{e.get('step')}", head, kids))
    return out


def review_node(key: str, r: dict) -> tuple:
    """The senior reviewer's verdict on a submit, with the problems it sent back."""
    probs = r.get("problems") or []
    who = pretty_model(r.get("model", ""))
    if r.get("verdict") == "fix":
        head = Text.assemble(("Reviewer: ", "bold #b6a0e0"), (who, "bold"),
                             f" read the task, the transcript and the diff, and sent the work back with {len(probs)} problem(s)")
    elif r.get("verdict") in ("open", "fixed"):
        head = Text.assemble(("Reviewer: ", "bold #b6a0e0"), (who, "bold"), " second look after the fix: "
                             + (f"{len(probs)} still open" if probs else "all fixed"))
    else:
        head = Text.assemble(("Reviewer: ", "bold #b6a0e0"), (who, "bold"), " approved the work")
    return (key, head, [(f"{key}/p{i}", Text(_short(p.get("what"), 150), style="#e2b55c"), [])
                        for i, p in enumerate(probs)])


def helper_node(parent: str, h: dict, workers: dict[str, list]) -> tuple:
    """A helper under the lead's turn that handed it its sub-task: its turns, then its report."""
    msgs = workers.get(f"worker_{h['id']}", [])
    turns = []
    for k, i in enumerate(i for i, m in enumerate(msgs) if i >= 2 and m.get("role") == "assistant"):
        thought, cmd = split_reply(str(msgs[i].get("content") or ""))
        nxt = msgs[i + 1] if i + 1 < len(msgs) else None
        ok = None if nxt is None else (True if nxt.get("role") == "exit" else summarize_output(str(nxt.get("content") or ""))[0])
        turns.append((f"{parent}/{h['id']}/t{k}", turn_summary(thought, cmd, ok), []))
    look = {"working": ("●", "#e2b55c"), "done": ("✔", "#7fb685"), "failed": ("✘", "#e07a68")}[h["state"]]
    if h["summary"]:
        turns.append((f"{parent}/{h['id']}/report", Text.assemble(("Report: ", "bold"), _short(h["summary"], 150)), []))
    head = Text.assemble((f"{look[0]} Helper: ", f"bold {look[1]}"), (pretty_model(h["model"]), "bold"),
                         f"  {_short(h['task'], 80)}  ", (f"{h['calls']} calls · {spend_text(h['spend'])}", "dim"))
    return (f"{parent}/{h['id']}", head, turns or [(f"{parent}/{h['id']}/start", Text("Starting …", style="#e2b55c"), [])])


# ---------- screens ----------

class NewRunScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss_none", "Cancel")]

    def compose(self) -> ComposeResult:
        configs = sorted(p.name for p in (ROOT / "configs").glob("phase2-*.json"))
        default_cfg = "phase2-droid.json" if "phase2-droid.json" in configs else configs[0]
        with VerticalScroll(id="dialog") as dialog:
            dialog.border_title = "New run"
            dialog.border_subtitle = "Esc to cancel"
            yield Label("Task")
            yield Select(task_choices(), value=J.SMOKE_TASK, allow_blank=False, id="task")
            yield Label("Evaluation setup (fills in the config, policy and limits below)")
            yield Select([("custom: set them yourself", "")] + [(s, s) for s in SETUPS], value="",
                         allow_blank=False, id="setup")
            yield Label("Backend config (tiers, planner, prices)")
            yield Select([(c, f"configs/{c}") for c in configs], value=f"configs/{default_cfg}",
                         allow_blank=False, id="config")
            yield Label("Routing policy")
            yield Select([(p, p) for p in J.POLICIES] + [(f"fixed:{i}", f"fixed:{i}") for i in range(4)],
                         value="laya", allow_blank=False, id="policy")
            with Horizontal(id="caps"):
                for key, val, hint in (("max_plan_steps", "5", "max steps"), ("worker_step_limit", "25", "turns/step"),
                                       ("step_idle_sec", "600", "stop step after N sec idle")):
                    with Vertical():
                        yield Label(hint)
                        yield Input(val, type="integer", id=key)
            yield Label("Job name")
            yield Input(id="job_name")
            yield Static(id="preview")
            with Horizontal(id="buttons"):
                yield Button("Start run", variant="success", id="start")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._setup_caps: dict = {}
        self._update()

    def _apply_setup(self, setup: str) -> None:
        if not setup:
            self._setup_caps = {}
            return
        cfg_select = self.query_one("#config", Select)
        v = setup_values(setup, [f"configs/{p.name}" for p in (ROOT / "configs").glob("phase2-*.json")])
        self.query_one("#policy", Select).value = v["policy"]
        if "config" in v:
            cfg_select.value = v["config"]
        for key in ("max_plan_steps", "worker_step_limit", "step_idle_sec"):
            if key in v["caps"]:
                self.query_one(f"#{key}", Input).value = v["caps"][key]
        self._setup_caps = v["caps"]

    def _values(self) -> dict:
        caps = {**getattr(self, "_setup_caps", {}), **{k: self.query_one(f"#{k}", Input).value.strip()
                for k in ("max_plan_steps", "worker_step_limit", "step_idle_sec")}}
        return {"task": self.query_one("#task", Select).value, "config": self.query_one("#config", Select).value,
                "policy": self.query_one("#policy", Select).value, "caps": caps,
                "job_name": self.query_one("#job_name", Input).value.strip()}

    def _default_name(self) -> str:
        v = self._values()
        task = str(v["task"]).split("/")[-1]
        backend = Path(str(v["config"])).stem.removeprefix("phase2-")
        return f"{task}-{backend}-{time.strftime('%m%d-%H%M%S')}"

    def _update(self) -> None:
        name = self.query_one("#job_name", Input)
        if not name.value or getattr(self, "_auto_name", None) == name.value:
            self._auto_name = name.value = self._default_name()
        v = self._values()
        cmd = J.build_command(v["task"], v["config"], v["policy"], v["caps"], v["job_name"])
        self.query_one("#preview", Static).update(Text(" ".join(cmd[2:]).replace(str(ROOT) + "/", ""), style="dim"))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "setup":
            self._apply_setup(str(event.value or ""))
        self._update()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "job_name":
            self._update()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            v = self._values()
            if not v["job_name"]:
                self.notify("Job name is empty", severity="error")
                return
            self.dismiss(v)
        else:
            self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class ResultsScreen(ModalScreen):
    """Compare: an evaluation's results by setup and task, refreshed while it runs."""
    BINDINGS = [Binding("escape,c,q", "dismiss_none", "Close")]

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.prefixes = R.prefixes(root)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="results") as panel:
            panel.border_title = "Compare results"
            panel.border_subtitle = "Esc to close"
            if self.prefixes:
                yield Select([(p, p) for p in self.prefixes], value=self.prefixes[0], allow_blank=False,
                             id="prefix")
            yield Static(id="summary")
            yield DataTable(id="by-setup", zebra_stripes=True, cursor_type="none")
            yield Label("Per task: each run's share of hidden tests passed (✓ = solved)", classes="section")
            yield DataTable(id="by-task", zebra_stripes=True, cursor_type="none")
            yield Static("Solved = all hidden tests pass. Cost = tokens x API list price (nothing is billed per token)."
                         " Hit a limit = ended by the time or command limit before submitting. Reviewed = got the"
                         " senior review.", classes="note")

    def on_mount(self) -> None:
        self.show()
        self.set_interval(30, self.show)

    def show(self) -> None:
        summary = self.query_one("#summary", Static)
        if not self.prefixes:
            summary.update(Text("No evaluations under jobs/ yet.", style=MUTED))
            return
        p = R.progress(self.root, str(self.query_one("#prefix", Select).value))
        summary.update(results_header(p))
        by_setup = self.query_one("#by-setup", DataTable)
        fill(by_setup, list(SETUP_COLUMNS), setup_rows(p))
        fill(self.query_one("#by-task", DataTable), *task_rows(p))

    def on_select_changed(self, event: Select.Changed) -> None:
        self.show()

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


def fill(table: DataTable, columns: list[str], rows: list[tuple]) -> None:
    """Replace a table's contents, each column as wide as its widest cell (rich cells do not auto-size)."""
    table.clear(columns=True)
    for i, col in enumerate(columns):
        width = max([len(col)] + [len(r[i].plain if isinstance(r[i], Text) else str(r[i])) for r in rows])
        table.add_column(col, width=width + 1)
    table.add_rows(rows)


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss_none", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help"):
            yield Static(HELP)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen):
    BINDINGS = [Binding("y", "yes", "Yes"), Binding("n,escape", "no", "No")]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Label(self.message)
            with Horizontal(id="buttons"):
                yield Button("Yes, stop it  [y]", variant="error", id="yes")
                yield Button("No  [n]", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


# ---------- app ----------

class HarnessTUI(App):
    TITLE = "MLR570 routed harness"
    CSS = """
    Screen { background: $background; }
    * { scrollbar-color: $panel-lighten-2; scrollbar-color-hover: $primary 60%; scrollbar-color-active: $primary;
        scrollbar-background: $background; scrollbar-background-hover: $background; scrollbar-size-horizontal: 1; }
    #sidebar { width: 42; background: $surface; padding: 1 1 0 1; }
    #sidebar Button { width: 100%; height: 3; margin: 0 0 1 0; border: none; }
    #compare { background: $panel; }
    #filter { margin: 0 0 1 0; border: round $panel-lighten-1; background: $surface; }
    #filter:focus { border: round $primary; }
    #jobs { height: 1fr; background: $surface; }
    #jobs > ListItem { padding: 0 1; margin: 0 0 1 0; background: $surface; }
    #jobs > ListItem.-highlight { background: $primary 18%; }
    #main { padding: 1 2 0 2; }
    #meta { height: auto; }
    #progress { height: auto; border: round $panel-lighten-2; padding: 0 1; margin: 1 0 1 0; }
    #roles { height: auto; padding: 1 1; }
    TabbedContent { height: 1fr; }
    RichLog, Tree { height: 1fr; background: $background; }
    NewRunScreen, ConfirmScreen, ResultsScreen, HelpScreen { align: center middle; }
    #dialog { width: 92; height: auto; max-height: 90%; border: round $primary; background: $surface; padding: 1 2; }
    #dialog Label { color: $text-muted; }
    #confirm { width: 70; height: auto; border: round $error; background: $surface; padding: 1 2; }
    #results { width: 94%; height: auto; max-height: 92%; border: round $primary; background: $surface; padding: 1 2; }
    #results DataTable { height: auto; max-height: 20; margin: 1 0; }
    #results .section { margin: 1 0 0 0; text-style: bold; }
    #results .note, #help { color: $text-muted; }
    #help { width: 100; height: auto; max-height: 90%; border: round $primary; background: $surface; padding: 1 2; }
    #title { text-style: bold; margin-bottom: 1; }
    #caps { height: auto; }
    #caps Vertical { height: auto; width: 1fr; }
    #buttons { height: auto; margin-top: 1; }
    #buttons Button { margin-right: 2; }
    #preview { margin-top: 1; color: $text-muted; }
    """
    BINDINGS = [Binding("n", "new_run", "New run"), Binding("c", "compare", "Compare results"),
                Binding("slash", "focus_filter", "Filter"), Binding("s", "stop_run", "Stop run"),
                Binding("r", "refresh", "Refresh"), Binding("question_mark", "help", "Help"),
                Binding("q", "quit", "Quit")]

    def __init__(self, root: Path = ROOT, launcher=J.start_run, stopper=J.stop_run, poll_sec: float = 2.0):
        super().__init__()
        self.root, self.launcher, self.stopper, self.poll_sec = root, launcher, stopper, poll_sec
        self.jobs: list[J.Job] = []
        self.selected: str | None = None
        self._list_sig = None
        self._view_sig = None
        self._collapsed: set[str] = set()
        self.query_text = ""
        self.shown: list[J.Job] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Button("+ New run  [n]", id="new", variant="primary")
                yield Button("Compare results  [c]", id="compare")
                yield Input(placeholder="filter: e.g. ho4 running  [/]", id="filter")
                yield ListView(id="jobs")
            with Vertical(id="main"):
                yield Static(id="meta")
                yield Static(id="progress")
                with TabbedContent(initial="tab-overview"):
                    with TabPane("Overview", id="tab-overview"):
                        yield Tree("run", id="overview")
                    with TabPane("Who did what", id="tab-activity"):
                        yield RichLog(id="story", wrap=True)
                    with TabPane("Steps", id="tab-steps"):
                        yield DataTable(id="steps", zebra_stripes=True, cursor_type="row")
                    with TabPane("Worker turns", id="tab-worker"):
                        yield RichLog(id="worker", wrap=True)
                    with TabPane("Details", id="tab-details"):
                        yield Static(id="roles")
                    with TabPane("Raw events", id="tab-raw"):
                        yield RichLog(id="feed", wrap=True)
                    with TabPane("Harbor log", id="tab-log"):
                        yield RichLog(id="log", wrap=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = "mlr570"  # other themes: Ctrl+P, "Change theme"
        self.query_one("#progress", Static).border_title = "Now"
        self.query_one("#steps", DataTable).add_columns("step", "goal", "routed by", "routed to",
                                                        "attempts", "status", "calls")
        await self.refresh_all()
        self.set_interval(self.poll_sec, self.refresh_all)

    # --- data refresh ---

    async def refresh_all(self) -> None:
        self.jobs = J.list_jobs(self.root / "jobs")
        self.shown = [j for j in self.jobs if job_matches(j.name, j.status, self.query_text)]
        running = sum(j.status == "running" for j in self.jobs)
        self.sub_title = f"{running} running · {len(self.jobs)} runs" + (
            f" · {len(self.shown)} match the filter" if self.query_text else "")
        sig = ([(j.name, j.status) for j in self.shown], self.query_text)
        if sig != self._list_sig:
            self._list_sig = sig
            lv = self.query_one("#jobs", ListView)
            await lv.clear()
            items = []
            for j in self.shown:
                items.append(ListItem(Label(job_label(j.name, j.status, j.reward))))
            await lv.extend(items)
            names = [j.name for j in self.shown]
            if self.shown and self.selected not in names:  # the filter hid it: show the first match instead
                self.selected = self.shown[0].name
                self._collapsed.clear()
            if self.selected in names:
                lv.index = names.index(self.selected)
        self.render_job()

    def current(self) -> J.Job | None:
        return next((j for j in self.jobs if j.name == self.selected), None)

    def render_job(self, force: bool = False) -> None:
        job = self.current()
        meta = self.query_one("#meta", Static)
        if not job:
            meta.update(Text("No jobs yet. Press n to start a run, or ? for help." if not self.jobs else
                             "No run matches the filter.", style="dim"))
            return
        events = J.read_events(job.agent_dir)
        workers = J.worker_files(job.agent_dir)
        log = self.root / "jobs" / f"{job.name}.log"
        self.query_one("#progress", Static).update(
            progress_panel(events, trace_now(job, events), job.status, job.reward))
        sig = (job.name, job.status, len(events), workers[-1].stat().st_mtime if workers else 0,
               log.stat().st_size if log.exists() else 0)
        if sig == self._view_sig and not force:
            return
        self._view_sig = sig
        tot = totals(events)
        head = Text(job.task.split("/")[-1] or job.name, style="bold")
        head.append(f"   {STATUS_ICON.get(job.status, '')} {STATUS_WORD.get(job.status, job.status)}",
                    style=STATUS_STYLE.get(job.status, ""))
        elapsed = (events[-1]["t"] if events else 0)
        try:
            prefix, setup, _, rep = R.split_name(job.name)
            what = f"evaluation {prefix} · setup {setup} · repeat {rep}"
        except ValueError:
            what = f"run {job.name}"
        limits = limits_text(job.caps)
        head.append(f"\n{what}" + (f"   ·   limits {limits}" if limits else ""), style=MUTED)
        head.append(f"\nconfig {Path(job.config).stem.removeprefix('phase2-') or '?'} · policy {job.policy or '?'}",
                    style=MUTED)
        head.append(f"\nSpent {spend_text(spend(events))} at API list price over {tot['calls']} model calls",
                    style=MUTED)
        if job.exception:
            head.append(f"\n{job.exception}", style="#e07a68")
        meta.update(head)
        roles = roles_text(events) if events else Text("nothing yet", style="dim")
        roles.append(f"\n\n{tot['calls']} model calls · {_k(tot['input_tokens'])} input tokens · "
                     f"{_k(tot['output_tokens'])} output tokens · {elapsed:.0f} s traced", style="dim")
        self.query_one("#roles", Static).update(roles)
        worker_msgs = {w.stem: J.read_worker(w) for w in workers}
        result = J._read_json(job.trial_dir / "result.json") if job.trial_dir else {}
        self._fill_overview(job, overview(events, worker_msgs, result or None))
        story = self.query_one("#story", RichLog)
        story.clear()
        for line in story_lines(events, worker_msgs):
            story.write(line)
        feed = self.query_one("#feed", RichLog)
        feed.clear()
        for line in activity_lines(events):
            feed.write(line)
        table = self.query_one("#steps", DataTable)
        table.clear()
        for row in step_rows(events):
            table.add_row(*row)
        wlog = self.query_one("#worker", RichLog)
        wlog.clear()
        if workers:
            wlog.write(Text(f"{workers[-1].stem}  (latest of {len(workers)})", style="bold"))
            for line in worker_lines(J.read_worker(workers[-1])):
                wlog.write(line)
        hlog = self.query_one("#log", RichLog)
        hlog.clear()
        if log.exists():
            for line in log.read_text(errors="replace").splitlines()[-200:]:
                hlog.write(Text(line))

    def _fill_overview(self, job: J.Job, rows: list[tuple]) -> None:
        tree = self.query_one("#overview", Tree)
        tree.show_root = True
        tree.clear()
        title = Text(f"{job.task.split('/')[-1] or job.name}  ", style="bold")
        title.append(f"{STATUS_ICON.get(job.status, '')} {job.status}", style=STATUS_STYLE.get(job.status, ""))
        tree.root.set_label(title)

        def add(parent, items):
            for key, label, children in items:
                if children:
                    node = parent.add(label, data=key, expand=key not in self._collapsed)
                    add(node, children)
                else:
                    parent.add_leaf(label, data=key)

        add(tree.root, rows)
        tree.root.expand()

    def on_tree_node_collapsed(self, event: Tree.NodeCollapsed) -> None:
        if event.node.data:
            self._collapsed.add(event.node.data)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        if event.node.data:
            self._collapsed.discard(event.node.data)

    # --- events and actions ---

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        lv = self.query_one("#jobs", ListView)
        if lv.index is not None and lv.index < len(self.shown):
            name = self.shown[lv.index].name
            if name != self.selected:
                self.selected = name
                self._collapsed.clear()
                self.render_job(force=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new":
            self.action_new_run()
        elif event.button.id == "compare":
            self.action_compare()

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self.query_text = event.value.strip()
            await self.refresh_all()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter":  # Enter: back to the list, so the keys work again
            self.query_one("#jobs", ListView).focus()

    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_compare(self) -> None:
        self.push_screen(ResultsScreen(self.root))

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_new_run(self) -> None:
        def started(values):
            if not values:
                return
            try:
                caps = {k: v for k, v in values["caps"].items() if v}
                pid = self.launcher(self.root, values["task"], values["config"], values["policy"], caps,
                                    values["job_name"])
            except Exception as e:  # noqa: BLE001 - shown to the user
                self.notify(f"Could not start: {e}", severity="error")
                return
            self.selected = values["job_name"]
            self._list_sig = None
            self.notify(f"Started {values['job_name']} (pid {pid})")
            self.call_later(self.refresh_all)

        self.push_screen(NewRunScreen(), started)

    def action_stop_run(self) -> None:
        job = self.current()
        if not job or job.status != "running":
            self.notify("Select a running job to stop it", severity="warning")
            return

        def confirmed(yes):
            if yes:
                done = self.stopper(job)
                self.notify(f"Stopped {job.name}: " + (", ".join(done) or "nothing was running"))
                self._list_sig = None
                self.call_later(self.refresh_all)

        self.push_screen(ConfirmScreen(f"Stop {job.name}? Its container is removed too."), confirmed)

    async def action_refresh(self) -> None:
        self._list_sig = self._view_sig = None
        await self.refresh_all()


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT), help="folder whose jobs/ to show (the demo uses demo/)")
    HarnessTUI(root=Path(ap.parse_args().root)).run()


if __name__ == "__main__":
    main()
