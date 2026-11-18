"""Shared plumbing for CLI-backed models (`droid exec`, `claude -p`) used as text-in / text-out models.

Each call is stateless: the conversation is serialized into one prompt, and the CLI prints one JSON
`result` object with the reply and usage. Both CLIs add their own system prompt and per-call
overhead, so token counts here are inflated versus a raw API.
"""
import contextlib
import functools
import json
import subprocess
import threading
import time

PROMPT_HEADER = ("You are acting as a plain language model inside another program. Do not use any "
                 "tools, do not read or write files. Reply only with the next ASSISTANT message for "
                 "the conversation below, following its instructions exactly.\n\n")


def backend_of(model: str) -> str:
    return model.split("/", 1)[0] if model.startswith(("droid/", "claude/", "ocx/")) else ""


def delta_text(messages: list[dict]) -> str:
    """The new user turns of a resumed CLI session, as one plain prompt (the session already holds the
    earlier turns and the model's own replies)."""
    return "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") != "assistant")


class CliSession:
    """One CLI-side conversation per worker attempt (`claude -p --resume`, `droid exec -s`), so each call
    sends only the new turns and the provider caches the rest. Measured 2026-10-25: a resumed call read
    21,488 of ~22.5k tokens from the cache (Claude) and 18,176 (Droid, ~5x fewer credits per call); the
    stateless single-message prompt read 0 and re-wrote the whole cache every call.
    Droid reports usage cumulatively over the session and Claude Code reports total_cost_usd cumulatively,
    so `per_call` turns running totals into this call's share."""

    def __init__(self, fork_from: dict | None = None):
        import tempfile

        self.id: str | None = None
        # resume finds sessions by working directory: a fork runs where its parent session lives
        self.dir = (fork_from or {}).get("dir") or tempfile.mkdtemp(prefix="phase2-session-")
        self.fork_from = fork_from    # {"id", "backend", "dir", "chars", "cost_total"} of the continued step
        self.seed: dict = {}          # running totals a fork inherits (Claude counts the parent's cost)
        self.system = ""      # the first call's system prompt, re-sent unchanged so the cached prefix holds
        self.seen = 0         # mini-swe-agent messages already in the session
        self.chars = 0        # rough size of the session's context
        self.totals: dict = {}
        self.guarded = False  # the previous reply was cut by guard_reply: tell the model on the next turn
        self.resets = 0

    def per_call(self, values: dict, session_id: str | None, keys) -> dict:
        if session_id != self.id:  # a new session starts its running totals from zero (or a fork's seed)
            self.totals = dict(self.seed)
        out = dict(values)
        for k in keys:
            total = values.get(k) or 0
            out[k] = total - self.totals.get(k, 0)
            self.totals[k] = total
        self.id = session_id or self.id
        return out

    def reset(self) -> None:
        self.id, self.totals, self.chars, self.fork_from, self.seed = None, {}, 0, None, {}
        self.resets += 1


def serialize(messages: list[dict], header: str = PROMPT_HEADER) -> str:
    parts = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):  # multimodal / structured content: keep the text parts
            content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        parts.append(f"### {m.get('role', 'user').upper()}\n{content or ''}")
    return header + "\n\n".join(parts) + "\n\n### ASSISTANT\n"


def _last_result(stdout: str | None) -> dict | None:
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return obj
    return None


def parse_result(proc, cli: str) -> dict:
    """Last JSON line with type == result; raises on a missing or error result."""
    result = _last_result(proc.stdout)
    if result is None or result.get("is_error"):
        raise RuntimeError(f"{cli} failed (rc={proc.returncode}): {(proc.stderr or proc.stdout or '')[-500:]}")
    return result


def run_group(argv, *, input=None, cwd=None, env=None, timeout=None, capture_output=True, text=True):
    """subprocess.run, but a timeout kills the CLI's whole process group. subprocess.run only kills
    the CLI itself; its helper processes keep the output pipe open and the call blocks past the
    timeout (a droid exec call ran 14 min against a 10 min timeout)."""
    import os
    import signal

    p = subprocess.Popen(argv, stdin=subprocess.PIPE if input is not None else None,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env, text=text,
                         start_new_session=True)
    try:
        out, err = p.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        raise subprocess.TimeoutExpired(argv, timeout) from None
    return subprocess.CompletedProcess(argv, p.returncode, out, err)

RETRY_WAIT_SEC = 5.0
QUICK_FAIL_MS = 30_000  # zero-turn failures faster than this are retried once


class ProviderUnavailable(RuntimeError):
    """The CLI failed before any model turn ran (Droid prints only "Exec failed"; its log shows
    "Connection error."). Each such failure already cost ~80 s inside the CLI, so it is not retried
    here, and mini-swe-agent must not retry it either: the step attempt ends and escalates."""


class ModelTimedOut(RuntimeError):
    """Two timeouts in a row (the call and its retry). The model is stuck writing very long replies or
    the provider stream stalled; mini-swe-agent must not retry it (up to 10 more 2x timeouts), so the
    step attempt ends and escalates at once."""


_sink = threading.local()


@contextlib.contextmanager
def report_failures(fn):
    """Within this thread, every failed CLI attempt is passed to `fn(info)` (for the trace)."""
    prev = getattr(_sink, "fn", None)
    _sink.fn = fn
    try:
        yield
    finally:
        _sink.fn = prev


def _report(**info) -> None:
    fn = getattr(_sink, "fn", None)
    if fn:
        fn(info)


RETRY_NOTE = ("Your previous reply took too long and was discarded; none of it was used. Reply again, much "
              "shorter: keep your reasoning brief and give only what was asked.")


CONNECTION_WAIT_SEC = 600  # how long a lost network connection may stall a call before it counts as a failure


def run_cli(call, cli: str, attempts: int = 2, model: str | None = None, retry_call=None,
            sleep=time.sleep) -> dict:
    """call() -> CompletedProcess. Retries once on a timeout (a provider stream that stalled); a second
    timeout raises ModelTimedOut, which ends the step attempt. Also retries once on a quick zero-turn
    failure. A slow zero-turn failure (provider unreachable) raises ProviderUnavailable at once. A lost
    connection ("Connection error", no turns run) waits and retries for up to CONNECTION_WAIT_SEC without using
    an attempt: 2026-10-29, a Wi-Fi switch ended over half of a 48-run evaluation's runs early. Every failed
    attempt is reported to the thread's failure sink."""
    timed_out, waited, i = False, 0.0, 0
    while i < attempts:
        t0 = time.perf_counter()
        try:
            # After a timeout, the retry tells the model why (Terminus: "NONE of the actions... were
            # performed"); an identical prompt tends to produce the same over-long reply.
            proc = retry_call() if (i and timed_out and retry_call) else call()
        except subprocess.TimeoutExpired as e:
            timed_out = True
            last = i == attempts - 1
            _report(cli=cli, model=model, attempt=i + 1, kind="timeout",
                    ms=round((time.perf_counter() - t0) * 1e3), error=f"no reply in {e.timeout:.0f} s",
                    will_retry=not last)
            if last:
                raise ModelTimedOut(f"{cli} timed out after {e.timeout:.0f} s, {attempts} times in a row") from e
            i += 1
            continue
        try:
            return parse_result(proc, cli)
        except RuntimeError as e:
            ran = (_last_result(proc.stdout) or {}).get("num_turns") or 0
            ms = (time.perf_counter() - t0) * 1e3
            if not ran and "connection error" in str(e).lower() and waited < CONNECTION_WAIT_SEC:
                pause = min(30.0 * 2 ** min(int(waited // 30), 2), CONNECTION_WAIT_SEC - waited)
                _report(cli=cli, model=model, attempt=i + 1, kind="connection", ms=round(ms),
                        error=f"{str(e)[-200:]} (waiting {pause:.0f} s for the network)", will_retry=True)
                sleep(pause)
                waited += pause
                continue
            # A quick zero-turn failure (transient "Invalid model" catalog blip) is cheap to retry once;
            # a slow one is the provider being unreachable (~80 s each) and fails fast.
            retry = not ran and ms < QUICK_FAIL_MS and i < attempts - 1
            kind = "error" if ran else ("transient" if ms < QUICK_FAIL_MS else "provider_unavailable")
            if "usage polic" in str(e).lower() or "[cyber]" in str(e):
                kind = "refused"  # the provider's safety classifier declined the request (logged for the paper)
            _report(cli=cli, model=model, attempt=i + 1, kind=kind, ms=round(ms), error=str(e)[-300:],
                    will_retry=retry)
            if retry:
                sleep(RETRY_WAIT_SEC)
                i += 1
                continue
            if ran:
                raise
            raise ProviderUnavailable(str(e)) from e
    raise ModelTimedOut(f"{cli}: no attempts left")

def openai_usage(u: dict, **extra) -> dict:
    """Anthropic-style usage -> OpenAI-style. prompt_tokens includes cache reads and cache writes."""
    cached = int(u.get("cache_read_input_tokens") or 0)
    prompt = int(u.get("input_tokens") or 0) + cached + int(u.get("cache_creation_input_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": int(u.get("output_tokens") or 0),
            "prompt_tokens_details": {"cached_tokens": cached}, **extra}


def complete_fn_for(model: str, cfg=None):
    """The CLI backend's complete(model, messages) for this model id, or None for opencodex models.
    With a config, the model's pinned reasoning effort is bound in."""
    from . import claude_code, droid, ocx

    if droid.is_droid(model):
        fn = droid.complete
    elif ocx.is_ocx(model):
        fn = ocx.complete
    elif claude_code.is_claude_code(model):
        fn = claude_code.complete
    else:
        return None
    if cfg is None:
        return fn
    extra = {"max_output_tokens": cfg.max_output_tokens} if fn in (claude_code.complete, ocx.complete) and cfg.max_output_tokens else {}
    return functools.partial(fn, effort=(cfg.reasoning_effort or {}).get(model), timeout=cfg.cli_timeout_for(model), **extra)

class BudgetExceeded(Exception):
    """The run's spend cap was reached during an attempt: the worker stops before its next model call
    (2026-10-27: an attempt already running took a run to 415k credits against a 300k cap)."""


class StepIdle(Exception):
    """No model reply for too long: ends the step attempt (mini-swe-agent does not retry it)."""


class IdleGuard:
    """Ends a step only when it stops making progress (no model reply for `idle_sec`), so a slow but
    working model keeps going. Checked before every model call, including mini-swe-agent's retries
    (up to 10, with backoff) after failed calls."""

    def __init__(self, idle_sec: float):
        self.idle_sec = idle_sec
        self.last = time.monotonic()

    def check(self) -> None:
        idle = time.monotonic() - self.last
        if self.idle_sec and idle > self.idle_sec:
            raise StepIdle(f"no model reply for {idle / 60:.0f} min (limit {self.idle_sec / 60:.0f} min)")

    def progress(self) -> None:
        self.last = time.monotonic()


def guard_reply(text: str) -> tuple[str, str]:
    """Stream-rule style guard (oh-my-pi TTSR; Claude Code retries malformed calls): weak models keep
    writing the conversation past their own turn ("### USER", fake <returncode> output) or put several
    commands in one reply. Cut at the first invented turn and keep only the first command block, so the
    reply parses as one action instead of a format error. Returns (text, reason or "")."""
    import re

    reasons = []
    cut = re.search(r"\n(### (USER|ASSISTANT|SYSTEM)\b|<returncode>)", text)
    if cut:
        text, _ = text[: cut.start()], reasons.append("invented_turn")
    blocks = list(re.finditer(r"```mswea_bash_command\s*.*?```", text, re.S))
    if len(blocks) > 1:
        text = text[: blocks[0].end()]
        reasons.append(f"{len(blocks)}_commands")
    return text, "+".join(reasons)


def remind(messages: list[dict], reminder: str, update: str = "") -> list[dict]:
    """Droid's recency trick: restate the rules at the newest end of the context on every call, after any
    live team update."""
    if not (reminder or update) or not messages or messages[-1].get("role") != "user":
        return messages
    last = messages[-1]
    tail = (f"\n\n{update}" if update else "") + (f"\n\n[Reminder] {reminder}" if reminder else "")
    return [*messages[:-1], {**last, "content": f"{last.get('content') or ''}{tail}"}]


def make_text_model(model: str, on_result, complete_fn=None, idle_sec: float = 0, context_budget_chars: int = 0,
                    reminder: str = "", step_limit: int = 0, session_budget_chars: int = 0,
                    fork_from: dict | None = None, team_feed=None, budget_fn=None, output_guard: bool = True,
                    session_nudge_chars: int = 0):
    """mini-swe-agent text-based model whose completions come from a CLI backend.
    `on_result(usage, text, ms)` is called after every completion."""
    import litellm
    from minisweagent.models.litellm_model import LitellmModel
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    complete_fn = complete_fn or complete_fn_for(model)
    guard = IdleGuard(idle_sec)
    calls = [0]
    session = CliSession(fork_from) if session_budget_chars else None

    def session_call(messages, note, update=""):
        """Send only the turns the CLI session has not seen; once the session outgrows its budget, start a
        new one from the compacted view (stable compaction: the fold happens rarely, so the cached prefix
        survives between folds instead of changing on every call)."""
        from .worker import compact_messages

        def size(ms):
            return sum(len(str(m.get("content") or "")) for m in ms)

        delta = [m for m in messages[session.seen:] if m.get("role") != "assistant"]
        if session.fork_from and not session.id:  # first call of a continued step: fork the parent session
            payload, kind = [m for m in messages if m.get("role") != "system"], "fork"
            session.chars = session.fork_from.get("chars", 0)
        elif session.id and session.chars + size(delta) <= session_budget_chars:
            if session.guarded:
                delta = [{"role": "user", "content": "(Only the first command of your last reply was run.)"}, *delta]
            if session_nudge_chars and session.chars >= getattr(session, "next_nudge", session_nudge_chars):
                # v3.6: restate the task by appending (the cached prefix survives); v2.8's restart did this by accident
                session.next_nudge = session.chars + session_nudge_chars
                first = next((str(m.get("content") or "") for m in messages if m.get("role") == "user"), "")
                delta = [{"role": "user", "content": "[The harness restates your task; keep going until all of it is "
                          "done and checked]\n" + first[:6000]}, *delta]
            payload, kind = delta, "resume"
        else:
            if session.id:
                session.reset()
            payload, kind = compact_messages(messages, context_budget_chars), "new"
        try:
            text, usage = complete_fn(model, remind(payload, note, update), session=session)
        except (StepIdle, ModelTimedOut):
            raise
        except Exception:
            if kind == "new":
                raise
            # a lost session or a failed fork: start again from the compacted view
            session.reset()  # a lost or broken session: start again from the compacted view
            payload, kind = compact_messages(messages, context_budget_chars), "new"
            text, usage = complete_fn(model, remind(payload, note, update), session=session)
        session.seen = len(messages)
        session.chars = (session.chars if kind in ("resume", "fork") else 0) + size(payload) + len(text)
        return text, {**usage, "session": kind}

    class CliTextModel(LitellmTextbasedModel):
        abort_exceptions = [*LitellmModel.abort_exceptions, StepIdle, ProviderUnavailable, ModelTimedOut,
                            BudgetExceeded]

        def _query(self, messages, **kwargs):
            from .worker import compact_messages

            guard.check()
            t0 = time.perf_counter()
            if budget_fn and (why := budget_fn()):
                raise BudgetExceeded(why)
            calls[0] += 1
            note = reminder
            left = step_limit - calls[0] if step_limit else 99
            if left <= 5:  # oh-my-pi's soft budget: warn before the hard limit ends the attempt
                note += (f" About {max(left, 0)} actions left: if you will not finish, publish what you found "
                         "and a blocker, then submit.")
            update = team_feed() if team_feed else ""
            text, usage = session_call(messages, note, update) if session else \
                complete_fn(model, remind(compact_messages(messages, context_budget_chars), note, update))
            if update:
                usage = {**usage, "team_update": update[:200]}
            guard.progress()
            if usage.get("truncated"):  # pi-mono runs none of a reply cut at the output limit
                text = text.split("```mswea_bash_command")[0] + "\n(reply cut at the output limit; nothing was run)"
            text, guarded = guard_reply(text) if output_guard else (text, "")
            guarded = "+".join(x for x in ("truncated" if usage.get("truncated") else "", guarded) if x)
            if session:
                session.guarded = bool(guarded)
            if guarded:
                usage = {**usage, "guarded": guarded}
            on_result(usage, text, (time.perf_counter() - t0) * 1e3)
            return litellm.ModelResponse(
                model=model,
                choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                usage={k: usage[k] for k in ("prompt_tokens", "completion_tokens")},
            )

        def _calculate_cost(self, response):
            # The Ledger prices CLI calls; litellm cannot resolve droid/ or claude/ ids and
            # prints a "Provider List" error on every lookup.
            return {"cost": 0.0}

    m = CliTextModel(model_name=model, cost_tracking="ignore_errors")
    m.cli_session = session
    m.idle_guard = guard  # a lead's helpers count as progress (phase2.delegate)
    return m
