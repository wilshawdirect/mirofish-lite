import pytest

from mirofish_lite import DEFAULT_PERSONAS, Persona, default_personas


def test_six_default_personas_with_expected_names():
    assert [p.name for p in DEFAULT_PERSONAS] == [
        "conservative",
        "growth",
        "risk-officer",
        "cash-flow",
        "contrarian",
        "newcomer",
    ]


def test_contrarian_is_advisory_and_the_other_five_count():
    by_name = {p.name: p for p in DEFAULT_PERSONAS}
    assert by_name["contrarian"].in_quorum is False
    assert sum(p.in_quorum for p in DEFAULT_PERSONAS) == 5


def test_defaults_have_unit_weight_and_no_hard_veto():
    assert all(p.weight == 1 and not p.hard_veto for p in DEFAULT_PERSONAS)


def test_default_prompts_are_substantial_and_distinct():
    prompts = [p.system_prompt for p in DEFAULT_PERSONAS]
    assert all(len(t) > 150 for t in prompts)
    assert len(set(prompts)) == len(prompts)


def test_default_personas_returns_a_fresh_list():
    a, b = default_personas(), default_personas()
    assert a == b and a is not b


@pytest.mark.parametrize(
    "name", ["", " lead", "bad\nname", "x]", "a" * 65, "semi;colon", "trailing\n", "nl-at-end\r\n", "tab\t"]
)
def test_bad_names_are_rejected(name):
    with pytest.raises(ValueError):
        Persona(name, "prompt")


@pytest.mark.parametrize("prompt", ["", "   ", None, 3])
def test_bad_prompts_are_rejected(prompt):
    with pytest.raises(ValueError):
        Persona("ok", prompt)


@pytest.mark.parametrize("weight", [0, -1, float("nan"), float("inf"), True, "2", None, 10**400, -(10**400)])
def test_bad_weights_are_rejected(weight):
    with pytest.raises(ValueError):
        Persona("ok", "prompt", weight=weight)


def test_flags_must_be_booleans():
    with pytest.raises(ValueError):
        Persona("ok", "prompt", in_quorum=1)
    with pytest.raises(ValueError):
        Persona("ok", "prompt", hard_veto="yes")


def test_round_trip_through_dict():
    p = Persona("custom", "Be careful.", weight=2.5, in_quorum=False, hard_veto=True)
    assert Persona.from_dict(p.to_dict()) == p


def test_from_dict_rejects_unknown_and_missing_fields():
    with pytest.raises(ValueError):
        Persona.from_dict({"name": "a", "system_prompt": "b", "colour": "red"})
    with pytest.raises(ValueError):
        Persona.from_dict({"name": "a"})


def test_prompt_hash_is_stable_and_changes_with_the_prompt():
    a = Persona("a", "one")
    assert a.prompt_sha256 == Persona("b", "one").prompt_sha256
    assert a.prompt_sha256 != Persona("a", "two").prompt_sha256
