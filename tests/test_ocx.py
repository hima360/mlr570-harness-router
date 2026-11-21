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


def test_streamed_reply_is_assembled_with_usage():
    from types import SimpleNamespace as N
    chunks = [N(choices=[N(delta=N(content="hel"))], usage=None), N(choices=[N(delta=N(content="lo"))], usage=None),
              N(choices=[], usage={"prompt_tokens": 9, "completion_tokens": 2,
                                   "prompt_tokens_details": {"cached_tokens": 4}})]
    seen = []

    def call(model, messages, **kw):
        seen.append(kw)
        return iter(chunks)
    text, usage = ocx.complete("ocx/m", [{"role": "user", "content": "x"}], call=call)
    assert text == "hello" and usage["prompt_tokens_details"]["cached_tokens"] == 4 and seen[0]["stream"] is True


def test_empty_incomplete_stream_is_retried_but_length_cut_is_not():
    from types import SimpleNamespace as N
    empty = [N(choices=[N(delta=N(content=None), finish_reason=None)], usage=None),
             N(choices=[], usage={"prompt_tokens": 5, "completion_tokens": 2000})]
    good = [N(choices=[N(delta=N(content="ok"), finish_reason="stop")], usage=None)]
    replies = [empty, good]
    waits = []
    text, _ = ocx.complete("ocx/m", [{"role": "user", "content": "x"}], call=lambda **kw: iter(replies.pop(0)),
                           sleep=waits.append)
    assert text == "ok" and len(waits) == 1
    cut = [N(choices=[N(delta=N(content=None), finish_reason="length")], usage=None)]
    text, _ = ocx.complete("ocx/m", [{"role": "user", "content": "x"}], call=lambda **kw: iter(cut),
                           sleep=lambda s: (_ for _ in ()).throw(AssertionError("retried a length cut")))
    assert text == ""


def test_reasoning_replay_keeps_reasoning_in_history_and_body():
    import json
    from phase2.cli_models import CliSession
    seen = []

    def rcall(model_id, history, effort, timeout, max_out):
        seen.append(ocx._responses_body(model_id, history, effort, max_out))
        return f"r{len(seen)}", {"type": "reasoning", "summary": [{"type": "summary_text", "text": "think"}]}, \
            {"prompt_tokens": 1, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 0}}
    s = CliSession()
    ocx.complete("ocx/zai/glm", [{"role": "system", "content": "sys"}, {"role": "user", "content": "a"}],
                 session=s, replay_reasoning=True, responses_call=rcall, effort="high")
    ocx.complete("ocx/zai/glm", [{"role": "user", "content": "b"}], session=s, replay_reasoning=True,
                 responses_call=rcall)
    body = seen[1]
    assert body["instructions"] == "sys" and body["reasoning"]["summary"] == "auto"
    assert [i.get("type") or i["role"] for i in body["input"]] == ["user", "reasoning", "assistant", "user"]
    assert "effort" in seen[0]["reasoning"] and json.dumps(body)


def test_responses_collect_text_reasoning_usage_and_incomplete():
    import json
    import pytest
    done = {"type": "response.completed", "response": {"output": [
        {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "t"}]},
        {"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
        "usage": {"input_tokens": 5, "output_tokens": 3, "input_tokens_details": {"cached_tokens": 2}}}}
    text, r, u = ocx._responses_collect(["event: x", "data: " + json.dumps(done)])
    assert text == "hi" and r["id"] == "rs_1" and u["prompt_tokens_details"]["cached_tokens"] == 2
    with pytest.raises(ocx.IncompleteReply):
        ocx._responses_collect(["data: {\"type\": \"response.created\"}"])
    cut = {"type": "response.incomplete", "response": {"output": [], "usage": {}}}
    assert ocx._responses_collect(["data: " + json.dumps(cut)])[0] == ""


def test_complete_fn_for_binds_replay_only_for_listed_models():
    from phase2.cli_models import complete_fn_for
    from phase2.config import load_config
    cfg = load_config(None, reasoning_replay_models=["ocx/zai/glm-5.3-flash"], max_output_tokens=0)
    assert complete_fn_for("ocx/zai/glm-5.3-flash", cfg).keywords.get("replay_reasoning") is True
    assert "replay_reasoning" not in complete_fn_for("ocx/anthropic/claude-opus-5-5", cfg).keywords
