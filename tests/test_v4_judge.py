"""v4 judge: decides from the builder's checks; no verdict is not a pass (fake model, no network)."""
from phase2.review import CHECKS_NOTE, senior_review


def asker(replies, seen):
    it = iter(replies)

    def ask(messages):
        seen.append(messages[-1]["content"])
        return next(it)
    return ask


def test_checks_go_into_the_prompt_and_ship_on_green():
    seen = []
    v, probs, _ = senior_review(asker(['{"verdict": "ship", "problems": []}'], seen), "TASK", "done", "", "",
                                checks="--- output\nPASS a\n--- exit code: 0", checks_path="/tmp/phase2_checks.sh")
    assert v == "ship" and probs == []
    assert "THE HARNESS RAN THE COLLEAGUE'S CHECK SCRIPT (/tmp/phase2_checks.sh)" in seen[0]
    assert "PASS a" in seen[0]


def test_old_default_ships_when_out_of_turns_v4_says_fix():
    replies = ['{"explore": "ls"}'] * 5
    v, _, _ = senior_review(asker(replies, []), "T", "", "", "", explore=lambda c: "x", max_explore=1)
    assert v == "ship"  # v2.8 behaviour kept when the switch is off
    v, probs, _ = senior_review(asker(replies, []), "T", "", "", "", explore=lambda c: "x", max_explore=1,
                                out_of_turns="fix")
    assert v == "fix" and "ran out of turns" in probs[0]["what"]


def test_unreadable_review_is_fix_only_under_v4():
    assert senior_review(asker(["no json here"], []), "T", "", "", "")[0] == "ship"
    assert senior_review(asker(["no json here"], []), "T", "", "", "", out_of_turns="fix")[0] == "fix"


def test_builder_note_names_the_path():
    assert "/tmp/phase2_checks.sh" in CHECKS_NOTE.format(path="/tmp/phase2_checks.sh")
