import json
import time

import pytest

from mirofish_lite import MockLLM, reply

SYS_A = "[persona: alpha]\nYou are alpha."
SYS_B = "[persona: beta]\nYou are beta."


def vote_of(text):
    return json.loads(text)["vote"]


def test_default_reply_is_a_yes():
    assert vote_of(MockLLM()(SYS_A, "u")) == "YES"


def test_per_persona_reply_and_fallback():
    llm = MockLLM({"alpha": reply("NO")}, default=reply("ABSTAIN"))
    assert vote_of(llm(SYS_A, "u")) == "NO"
    assert vote_of(llm(SYS_B, "u")) == "ABSTAIN"


def test_star_key_is_a_fallback_for_unlisted_personas():
    llm = MockLLM({"*": reply("NO")})
    assert vote_of(llm(SYS_A, "u")) == "NO"


def test_dict_replies_are_json_encoded():
    llm = MockLLM({"alpha": {"vote": "NO", "reason": "r", "confidence": 1}})
    assert json.loads(llm(SYS_A, "u"))["vote"] == "NO"


def test_sequences_are_consumed_in_order_and_the_last_repeats():
    llm = MockLLM({"alpha": [reply("YES"), reply("NO")]})
    assert [vote_of(llm(SYS_A, "u")) for _ in range(4)] == ["YES", "NO", "NO", "NO"]


def test_exception_instances_are_raised():
    llm = MockLLM({"alpha": ValueError("boom")})
    with pytest.raises(ValueError, match="boom"):
        llm(SYS_A, "u")


def test_callables_receive_the_prompts():
    seen = []
    llm = MockLLM({"alpha": lambda s, u: seen.append((s, u)) or reply("NO")})
    assert vote_of(llm(SYS_A, "the user prompt")) == "NO"
    assert seen == [(SYS_A, "the user prompt")]


def test_rules_are_checked_before_persona_entries():
    llm = MockLLM({"alpha": reply("YES")}, rules=[(lambda persona, s, u: "danger" in u, reply("NO"))])
    assert vote_of(llm(SYS_A, "this is fine")) == "YES"
    assert vote_of(llm(SYS_A, "this is danger")) == "NO"


def test_calls_are_recorded_with_the_persona_name():
    llm = MockLLM()
    llm(SYS_A, "one")
    llm(SYS_B, "two")
    assert [c.persona for c in llm.calls] == ["alpha", "beta"]
    assert [c.user for c in llm.calls_for("beta")] == ["two"]


def test_unlabelled_system_prompt_has_no_persona():
    llm = MockLLM()
    llm("plain system prompt", "u")
    assert llm.calls[0].persona is None


def test_delay_can_be_per_persona():
    llm = MockLLM(delay={"alpha": 0.15})
    t0 = time.monotonic()
    llm(SYS_B, "u")
    fast = time.monotonic() - t0
    t0 = time.monotonic()
    llm(SYS_A, "u")
    slow = time.monotonic() - t0
    assert slow >= 0.1 and fast < slow


def test_from_spec_builds_votes_errors_and_garbage():
    llm = MockLLM.from_spec("alpha=no, beta=garbage, *=abstain")
    assert vote_of(llm(SYS_A, "u")) == "NO"
    assert "JSON" in llm(SYS_B, "u")
    assert vote_of(llm("[persona: other]\nx", "u")) == "ABSTAIN"
    with pytest.raises(RuntimeError):
        MockLLM.from_spec("alpha=error")(SYS_A, "u")


def test_empty_spec_means_everyone_says_yes():
    assert vote_of(MockLLM.from_spec("")(SYS_A, "u")) == "YES"


@pytest.mark.parametrize("spec", ["alpha", "alpha=MAYBE", "=NO"])
def test_bad_specs_raise(spec):
    with pytest.raises(ValueError):
        MockLLM.from_spec(spec)


def test_kind_is_mock():
    assert MockLLM.kind == "mock" and MockLLM().kind == "mock"
