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


def run_cli(call, cli: str, attempts: int = 2, model: str | None = None) -> dict:
    """call() -> CompletedProcess. Retries once on a timeout (a provider stream that stalled); a second
    timeout raises ModelTimedOut, which ends the step attempt. Also retries once on a quick zero-turn
    failure. A slow zero-turn failure (provider unreachable) raises ProviderUnavailable at once. Every
    failed attempt is reported to the thread's failure sink."""
    for i in range(attempts):
        t0 = time.perf_counter()
        try:
            proc = call()
        except subprocess.TimeoutExpired as e:
            last = i == attempts - 1
            _report(cli=cli, model=model, attempt=i + 1, kind="timeout",
                    ms=round((time.perf_counter() - t0) * 1e3), error=f"no reply in {e.timeout:.0f} s",
                    will_retry=not last)
            if last:
                raise ModelTimedOut(f"{cli} timed out after {e.timeout:.0f} s, {attempts} times in a row") from e
            continue
        try:
            return parse_result(proc, cli)
        except RuntimeError as e:
            ran = (_last_result(proc.stdout) or {}).get("num_turns") or 0
            ms = (time.perf_counter() - t0) * 1e3
            # A quick zero-turn failure (transient "Invalid model" catalog blip) is cheap to retry once;
            # a slow one is the provider being unreachable (~80 s each) and fails fast.
            retry = not ran and ms < QUICK_FAIL_MS and i < attempts - 1
            kind = "error" if ran else ("transient" if ms < QUICK_FAIL_MS else "provider_unavailable")
            _report(cli=cli, model=model, attempt=i + 1, kind=kind, ms=round(ms), error=str(e)[-300:],
                    will_retry=retry)
            if retry:
                time.sleep(RETRY_WAIT_SEC)
                continue
            if ran:
                raise
            raise ProviderUnavailable(str(e)) from e

def openai_usage(u: dict, **extra) -> dict:
    """Anthropic-style usage -> OpenAI-style. prompt_tokens includes cache reads and cache writes."""
    cached = int(u.get("cache_read_input_tokens") or 0)
    prompt = int(u.get("input_tokens") or 0) + cached + int(u.get("cache_creation_input_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": int(u.get("output_tokens") or 0),
            "prompt_tokens_details": {"cached_tokens": cached}, **extra}


def complete_fn_for(model: str, cfg=None):
    """The CLI backend's complete(model, messages) for this model id, or None for opencodex models.
    With a config, the model's pinned reasoning effort is bound in."""
    from . import claude_code, droid

    if droid.is_droid(model):
        fn = droid.complete
    elif claude_code.is_claude_code(model):
        fn = claude_code.complete
    else:
        return None
    if cfg is None:
        return fn
    return functools.partial(fn, effort=(cfg.reasoning_effort or {}).get(model), timeout=cfg.cli_timeout_sec)

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


def make_text_model(model: str, on_result, complete_fn=None, idle_sec: float = 0):
    """mini-swe-agent text-based model whose completions come from a CLI backend.
    `on_result(usage, text, ms)` is called after every completion."""
    import litellm
    from minisweagent.models.litellm_model import LitellmModel
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    complete_fn = complete_fn or complete_fn_for(model)
    guard = IdleGuard(idle_sec)

    class CliTextModel(LitellmTextbasedModel):
        abort_exceptions = [*LitellmModel.abort_exceptions, StepIdle, ProviderUnavailable, ModelTimedOut]

        def _query(self, messages, **kwargs):
            guard.check()
            t0 = time.perf_counter()
            text, usage = complete_fn(model, messages)
            guard.progress()
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

    return CliTextModel(model_name=model, cost_tracking="ignore_errors")
