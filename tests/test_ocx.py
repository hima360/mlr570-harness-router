"""opencodex text backend: session history, fork, usage shape (fake litellm call, no network)."""
from types import SimpleNamespace

from phase2 import ocx
from phase2.cli_models import CliSession, backend_of, complete_fn_for


def fake_call(seen):
    def call(model, messages, **kw):
        seen.append({"model": model, "messages": [dict(m) for m in messages], **kw})
        usage = {"prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 80}}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=f"reply{len(seen)}"))],
                               usage=usage)
    return call


def test_dispatch_and_backend():
    assert complete_fn_for("ocx/zai/glm-5.3-flash") is ocx.complete
    assert backend_of("ocx/anthropic/claude-opus-5-5") == "ocx"


def test_stateless_call_strips_prefix_and_reads_usage():
    seen = []
    text, usage = ocx.complete("ocx/zai/glm-5.3-flash", [{"role": "user", "content": "hi"}], call=fake_call(seen))
    assert text == "reply1"
    assert seen[0]["model"] == "openai/zai/glm-5.3-flash"
    assert usage == {"prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 80}}
    assert "reasoning_effort" not in seen[0]


def test_session_resends_history_with_new_turns_only_from_caller():
    seen, s = [], CliSession()
    ocx.complete("ocx/m", [{"role": "system", "content": "sys"}, {"role": "user", "content": "a"}],
                 session=s, call=fake_call(seen), effort="medium")
    assert s.id and seen[0]["reasoning_effort"] == "medium"
    ocx.complete("ocx/m", [{"role": "user", "content": "b"}], session=s, call=fake_call(seen))
    assert [m["content"] for m in seen[1]["messages"]] == ["sys", "a", "reply1", "b"]


def test_fork_continues_parent_history():
    seen, parent = [], CliSession()
    ocx.complete("ocx/m", [{"role": "user", "content": "a"}], session=parent, call=fake_call(seen))
    child = CliSession(fork_from={"id": parent.id, "backend": "ocx", "dir": parent.dir})
    ocx.complete("ocx/m", [{"role": "user", "content": "c"}], session=child, call=fake_call(seen))
    assert [m["content"] for m in seen[1]["messages"]] == ["a", "reply1", "c"]
    assert child.id != parent.id


def test_retries_rate_limits_then_succeeds():
    seen, waits, fails = [], [], [2]

    def flaky(model, messages, **kw):
        if fails[0]:
            fails[0] -= 1
            raise RuntimeError("Provider error 429: Rate limit reached for requests")
        return fake_call(seen)(model, messages, **kw)
    text, _ = ocx.complete("ocx/m", [{"role": "user", "content": "x"}], call=flaky, sleep=waits.append)
    assert text == "reply1" and waits == [20, 40]


def test_does_not_retry_real_errors():
    import pytest

    def bad(model, messages, **kw):
        raise ValueError("invalid model id")
    with pytest.raises(ValueError):
        ocx.complete("ocx/m", [{"role": "user", "content": "x"}], call=bad, sleep=lambda s: None)
