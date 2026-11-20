"""v4.1: the Opus judge through opencodex sometimes writes invented tool calls and results in one reply."""
import json

from phase2.planner import _explore_request
from phase2.review import senior_review


def test_first_explore_is_taken_from_invented_tool_calls():
    reply = ('<invoke name="bash_tool"><parameter name="command">{"explore": "cat a.py"}</parameter></invoke>'
             '<invoke_result>made up</invoke_result><invoke>{"explore": "cat b.py"}</invoke>')
    assert _explore_request(reply) == "cat a.py"
    assert _explore_request('<invoke name="Bash"><parameter name="command">sed -n 1,9p f</parameter></invoke>') == "sed -n 1,9p f"
    assert _explore_request('{"verdict": "ship", "problems": []}') is None


def test_judge_history_keeps_only_the_request_and_real_output():
    replies = ['<invoke>{"explore": "ls"}</invoke><invoke_result>FAKE</invoke_result>',
               '{"verdict": "fix", "problems": [{"what": "x", "evidence": "", "fix": ""}]}']
    seen = []

    def ask(msgs):
        seen.append([m["content"] for m in msgs])
        return replies[len(seen) - 1]
    verdict, probs, n = senior_review(ask, "task", "", "", "", explore=lambda c: "REAL", max_explore=3,
                                      out_of_turns="fix")
    assert verdict == "fix" and n == 1
    assert json.loads(seen[1][1]) == {"explore": "ls"} and "FAKE" not in "".join(seen[1]) and "REAL" in seen[1][2]
