"""Terminal UI for the routed harness: start runs, watch who does what live, browse and stop jobs.

  .venv/bin/python -m phase2.tui

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
from textual.widgets import (Button, DataTable, Footer, Header, Input, Label, ListItem, ListView, RichLog,
                             Select, Static, TabbedContent, TabPane)

from . import jobs as J

ROOT = Path(__file__).resolve().parents[1]
STATUS_STYLE = {"running": "bold yellow", "passed": "bold green", "failed": "bold red", "error": "bold red",
                "stopped": "dim", "incomplete": "dim"}
STATUS_ICON = {"running": "●", "passed": "✓", "failed": "✗", "error": "!", "stopped": "■", "incomplete": "…"}
ROLE_STYLE = {"PLANNER": "bold magenta", "ROUTER": "bold cyan", "WORKER": "bold blue", "VERIFIER": "bold green",
              "ESCALATE": "bold yellow", "DONE": "bold white"}
LEGACY_ROUTER = {"laya": "Laya (laya_mlx, local MLX)", "arch": "Arch-Router-1.5B (katanemo, local)",
                 "fixed": "fixed policy (no model)", "random": "random policy (no model)"}
CMD_RE = re.compile(r"```mswea_bash_command\s*(.*?)(?:```|$)", re.S)


# ---------- pure renderers (tested without a terminal) ----------

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
        elif ev == "llm_call" and e.get("role") == "worker":
            m = CMD_RE.search(e.get("reply", ""))
            said = f"$ {m.group(1).strip()}" if m else e.get("reply", "")
            out.append(_line(t, "WORKER", e.get("model", "?"),
                             f"{e.get('step')}  {_secs(e.get('ms'))}  {_usage(e)}  {said[:140]}"))
        elif ev == "attempt":
            check = e.get("check") or ""
            how = f"check `{check[:60]}`" if check else "no check, trusts the worker's submit"
            verdict = Text("PASS", style="bold green") if e.get("passed") else Text("FAIL", style="bold red")
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
            out.append(Text("model ▸ ", style="bold blue") + Text(content.strip()))
        elif role == "exit":
            out.append(Text(f"exit ▸ {m.get('extra', {}).get('exit_status', '')} {content[:300]}", style="bold"))
        else:
            out.append(Text("shell ◂ ", style="bold green") + Text(content.strip()[:max_obs], style="dim"))
        out.append(Text(""))
    return out


# ---------- screens ----------

class NewRunScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss_none", "Cancel")]

    def compose(self) -> ComposeResult:
        configs = sorted(p.name for p in (ROOT / "configs").glob("phase2-*.json"))
        default_cfg = "phase2-droid.json" if "phase2-droid.json" in configs else configs[0]
        with VerticalScroll(id="dialog"):
            yield Label("New run", id="title")
            yield Label("Task")
            yield Select(J.TASKS, value=J.SMOKE_TASK, allow_blank=False, id="task")
            yield Label("Backend config (tiers, planner, prices)")
            yield Select([(c, f"configs/{c}") for c in configs], value=f"configs/{default_cfg}",
                         allow_blank=False, id="config")
            yield Label("Routing policy")
            yield Select([(p, p) for p in J.POLICIES] + [(f"fixed:{i}", f"fixed:{i}") for i in range(4)],
                         value="laya", allow_blank=False, id="policy")
            with Horizontal(id="caps"):
                for key, val, hint in (("max_plan_steps", "5", "max steps"), ("worker_step_limit", "25", "turns/step"),
                                       ("step_wall_time_sec", "900", "sec/step")):
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
        self._update()

    def _values(self) -> dict:
        caps = {k: self.query_one(f"#{k}", Input).value.strip()
                for k in ("max_plan_steps", "worker_step_limit", "step_wall_time_sec")}
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
    #sidebar { width: 38; border-right: solid $panel-lighten-2; padding: 0 1; }
    #sidebar Button { width: 100%; margin: 1 0; }
    #jobs { height: 1fr; }
    #main { padding: 0 1; }
    #meta { height: auto; padding: 1 0 0 0; }
    #roles { height: auto; border: round $panel-lighten-2; padding: 0 1; margin: 1 0 0 0; }
    TabbedContent { height: 1fr; }
    RichLog { height: 1fr; }
    NewRunScreen, ConfirmScreen { align: center middle; }
    #dialog { width: 90; height: auto; max-height: 90%; border: thick $accent; background: $surface; padding: 1 2; }
    #confirm { width: 70; height: auto; border: thick $error; background: $surface; padding: 1 2; }
    #title { text-style: bold; margin-bottom: 1; }
    #caps { height: auto; }
    #caps Vertical { height: auto; width: 1fr; }
    #buttons { height: auto; margin-top: 1; }
    #buttons Button { margin-right: 2; }
    #preview { margin-top: 1; }
    """
    BINDINGS = [Binding("n", "new_run", "New run"), Binding("s", "stop_run", "Stop run"),
                Binding("r", "refresh", "Refresh"), Binding("q", "quit", "Quit")]

    def __init__(self, root: Path = ROOT, launcher=J.start_run, stopper=J.stop_run, poll_sec: float = 2.0):
        super().__init__()
        self.root, self.launcher, self.stopper, self.poll_sec = root, launcher, stopper, poll_sec
        self.jobs: list[J.Job] = []
        self.selected: str | None = None
        self._list_sig = None
        self._view_sig = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Button("+ New run  [n]", id="new", variant="primary")
                yield ListView(id="jobs")
            with Vertical(id="main"):
                yield Static(id="meta")
                yield Static(id="roles")
                with TabbedContent(initial="tab-activity"):
                    with TabPane("Who did what", id="tab-activity"):
                        yield RichLog(id="feed", wrap=True)
                    with TabPane("Steps", id="tab-steps"):
                        yield DataTable(id="steps", zebra_stripes=True, cursor_type="row")
                    with TabPane("Worker turns", id="tab-worker"):
                        yield RichLog(id="worker", wrap=True)
                    with TabPane("Harbor log", id="tab-log"):
                        yield RichLog(id="log", wrap=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#steps", DataTable).add_columns("step", "goal", "routed by", "routed to",
                                                        "attempts", "status", "calls")
        await self.refresh_all()
        self.set_interval(self.poll_sec, self.refresh_all)

    # --- data refresh ---

    async def refresh_all(self) -> None:
        self.jobs = J.list_jobs(self.root / "jobs")
        sig = [(j.name, j.status) for j in self.jobs]
        if sig != self._list_sig:
            self._list_sig = sig
            lv = self.query_one("#jobs", ListView)
            await lv.clear()
            items = []
            for j in self.jobs:
                label = Text(f"{STATUS_ICON.get(j.status, '?')} ", style=STATUS_STYLE.get(j.status, ""))
                label.append(j.name)
                label.append(f"\n  {j.status}" + (f" · reward {j.reward}" if j.reward is not None else ""), style="dim")
                items.append(ListItem(Label(label)))
            await lv.extend(items)
            if self.selected is None and self.jobs:
                self.selected = self.jobs[0].name
            names = [j.name for j in self.jobs]
            if self.selected in names:
                lv.index = names.index(self.selected)
        self.render_job()

    def current(self) -> J.Job | None:
        return next((j for j in self.jobs if j.name == self.selected), None)

    def render_job(self, force: bool = False) -> None:
        job = self.current()
        meta = self.query_one("#meta", Static)
        if not job:
            meta.update(Text("No jobs yet. Press n to start a run.", style="dim"))
            return
        events = J.read_events(job.agent_dir)
        workers = J.worker_files(job.agent_dir)
        log = self.root / "jobs" / f"{job.name}.log"
        sig = (job.name, job.status, len(events), workers[-1].stat().st_mtime if workers else 0,
               log.stat().st_size if log.exists() else 0)
        if sig == self._view_sig and not force:
            return
        self._view_sig = sig
        tot = totals(events)
        head = Text(job.name, style="bold")
        head.append(f"  {STATUS_ICON.get(job.status, '')} {job.status}", style=STATUS_STYLE.get(job.status, ""))
        if job.reward is not None:
            head.append(f"  reward {job.reward}", style="bold")
        elapsed = (events[-1]["t"] if events else 0)
        head.append(f"\ntask {job.task}   backend {Path(job.config).name or '?'}   policy {job.policy or '?'}"
                    f"   caps {job.caps or '-'}", style="dim")
        head.append(f"\n{tot['calls']} model calls · {_k(tot['input_tokens'])} in · {_k(tot['output_tokens'])} out"
                    + (f" · {_k(tot['factory_credits'])} Factory credits" if tot["factory_credits"] else "")
                    + (f" · ${tot['cost_usd']:.4f} list price" if tot["cost_usd"] else "")
                    + f" · {elapsed:.0f}s traced")
        if job.exception:
            head.append(f"\n{job.exception}", style="red")
        meta.update(head)
        self.query_one("#roles", Static).update(roles_text(events) if events else
                                                Text("waiting for the trace (container build, Laya load) ...",
                                                     style="dim"))
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

    # --- events and actions ---

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        lv = self.query_one("#jobs", ListView)
        if lv.index is not None and lv.index < len(self.jobs):
            name = self.jobs[lv.index].name
            if name != self.selected:
                self.selected = name
                self.render_job(force=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new":
            self.action_new_run()

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
    HarnessTUI().run()


if __name__ == "__main__":
    main()
