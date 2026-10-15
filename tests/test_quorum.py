import pytest
from helpers import make_vote

from mirofish_lite import DEFAULT_PERSONAS, Outcome, Persona, QuorumRule, default_k, evaluate


def votes(spec, **kw):
    """spec like 'YYYNA' -> five votes named p0..p4"""
    names = {"Y": "YES", "N": "NO", "A": "ABSTAIN"}
    return [make_vote(f"p{i}", names[c], **kw) for i, c in enumerate(spec)]


def outcome(spec, k=4, **kw):
    return evaluate(votes(spec), QuorumRule(k=k, **kw)).outcome


def test_four_of_five_yes_approves():
    assert outcome("YYYYN") is Outcome.APPROVE


def test_unanimous_yes_approves():
    assert outcome("YYYYY") is Outcome.APPROVE


def test_three_yes_one_no_one_abstain_is_no_quorum():
    assert outcome("YYYNA") is Outcome.NO_QUORUM


def test_three_yes_two_abstain_is_no_quorum():
    assert outcome("YYYAA") is Outcome.NO_QUORUM


def test_three_yes_two_no_is_a_veto_because_k_is_unreachable():
    assert outcome("YYYNN") is Outcome.VETO


def test_all_abstain_is_no_quorum():
    assert outcome("AAAAA") is Outcome.NO_QUORUM


def test_all_no_is_a_veto():
    assert outcome("NNNNN") is Outcome.VETO


def test_tie_never_approves():
    # k=2 of 4 eligible: 2 YES vs 2 NO reaches k but is a tie
    result = evaluate(votes("YYNN"), QuorumRule(k=2))
    assert result.outcome is Outcome.NO_QUORUM
    assert "tie" in result.rationale


def test_k_reached_but_no_outweighs_yes_is_a_veto():
    assert outcome("YYNNN", k=2) is Outcome.VETO


def test_low_k_still_requires_yes_to_outnumber_no():
    assert outcome("YYYNN", k=2) is Outcome.APPROVE
    assert outcome("YYNNA", k=2) is Outcome.NO_QUORUM  # 2 YES reach k=2 but tie 2 NO


def test_weights_count_in_the_tally():
    vs = [
        make_vote("heavy", "YES", weight=3),
        make_vote("a", "NO"),
        make_vote("b", "NO"),
        make_vote("c", "ABSTAIN"),
    ]
    # YES 3 vs NO 2, k=3 -> approve even though only one persona said YES
    assert evaluate(vs, QuorumRule(k=3)).outcome is Outcome.APPROVE
    # the same heavy persona saying NO sinks it
    vs[0] = make_vote("heavy", "NO", weight=3)
    assert evaluate(vs, QuorumRule(k=3)).outcome is Outcome.VETO


def test_fractional_weights_tolerate_float_error():
    vs = [make_vote("a", "YES", weight=0.1), make_vote("b", "YES", weight=0.2), make_vote("c", "ABSTAIN", weight=0.7)]
    # 0.1 + 0.2 == 0.30000000000000004 in floating point; k=0.3 must still be met
    assert evaluate(vs, QuorumRule(k=0.3)).outcome is Outcome.APPROVE


def test_hard_veto_overrides_a_unanimous_yes_tally():
    vs = votes("YYYY") + [make_vote("guard", "NO", hard_veto=True)]
    result = evaluate(vs, QuorumRule(k=4))
    assert result.outcome is Outcome.VETO
    assert "guard" in result.rationale


def test_hard_veto_persona_voting_yes_does_not_veto():
    vs = votes("YYYY") + [make_vote("guard", "YES", hard_veto=True)]
    assert evaluate(vs, QuorumRule(k=4)).outcome is Outcome.APPROVE


def test_hard_veto_works_for_an_out_of_quorum_persona():
    vs = votes("YYYYY") + [make_vote("watcher", "NO", in_quorum=False, hard_veto=True)]
    assert evaluate(vs, QuorumRule(k=4)).outcome is Outcome.VETO


def test_out_of_quorum_persona_is_not_counted():
    vs = votes("YYYY") + [make_vote("adv", "NO", in_quorum=False)]
    result = evaluate(vs, QuorumRule(k=4))
    assert result.outcome is Outcome.APPROVE
    assert result.tally.eligible == 4
    assert result.tally.advisory == {"yes": 0, "no": 1, "abstain": 0}


def test_out_of_quorum_yes_cannot_make_up_a_shortfall():
    vs = votes("YYYAA") + [make_vote("adv", "YES", in_quorum=False), make_vote("adv2", "YES", in_quorum=False)]
    assert evaluate(vs, QuorumRule(k=4)).outcome is Outcome.NO_QUORUM


def test_of_all_counts_every_persona():
    vs = votes("YYYA") + [make_vote("adv", "YES", in_quorum=False)]
    assert evaluate(vs, QuorumRule(k=4, of="in_quorum")).outcome is Outcome.NO_QUORUM
    assert evaluate(vs, QuorumRule(k=4, of="all")).outcome is Outcome.APPROVE


def test_tally_reports_weights_as_plain_numbers():
    t = evaluate(votes("YYYYN"), QuorumRule(k=4)).tally.to_dict()
    assert t["yes"] == 4 and t["no"] == 1 and isinstance(t["yes"], int)
    assert t["k"] == 4 and t["eligible_weight"] == 5


@pytest.mark.parametrize("k", [0, -1, float("nan"), float("inf"), True, "4", None])
def test_invalid_k_is_rejected(k):
    with pytest.raises(ValueError):
        QuorumRule(k=k)


def test_invalid_of_is_rejected():
    with pytest.raises(ValueError):
        QuorumRule(k=1, of="everyone")


def test_outcome_exit_codes():
    assert [o.exit_code for o in (Outcome.APPROVE, Outcome.VETO, Outcome.NO_QUORUM)] == [0, 1, 2]


def test_k_above_the_eligible_weight_is_no_quorum_not_a_misleading_veto():
    result = evaluate(votes("YYY"), QuorumRule(k=4))
    assert result.outcome is Outcome.NO_QUORUM
    assert "exceeds" in result.rationale


def test_no_outnumbering_yes_with_k_still_reachable_is_no_quorum_not_veto():
    # k=2 of 5: 1 YES, 2 NO, 2 ABSTAIN. NO > YES, but approval is still arithmetically possible,
    # so the label is NO_QUORUM (exit 2). It never approves; only the label differs from a veto.
    result = evaluate(votes("YNNAA"), QuorumRule(k=2))
    assert result.outcome is Outcome.NO_QUORUM
    assert "still arithmetically possible" in result.rationale


def test_veto_needs_k_unreachable_or_k_met_but_outweighed():
    assert outcome("YNNNA", k=2) is Outcome.NO_QUORUM  # 2 weight can still say YES: not yet impossible
    assert outcome("YNNNN", k=2) is Outcome.VETO  # only 1 can still say YES, below k
    assert outcome("YYNNN", k=2) is Outcome.VETO  # k met but NO outweighs YES


def test_substituted_no_only_triggers_a_hard_veto_when_the_policy_says_so():
    vs = votes("YYYY") + [make_vote("guard", "NO", hard_veto=True, status="error")]
    assert evaluate(vs, QuorumRule(k=4)).outcome is Outcome.VETO
    assert evaluate(vs, QuorumRule(k=4), synthetic_hard_veto=False).outcome is Outcome.APPROVE
    real = votes("YYYY") + [make_vote("guard", "NO", hard_veto=True)]
    assert evaluate(real, QuorumRule(k=4), synthetic_hard_veto=False).outcome is Outcome.VETO


@pytest.mark.parametrize("weight, expected", [(5, 4), (6, 4), (7, 5), (9, 6), (10, 7), (12, 8), (30, 20)])
def test_default_k_scales_with_the_board(weight, expected):
    assert default_k(weight) == expected


def test_default_rule_for_the_default_personas_is_four_of_five():
    rule = QuorumRule.default_for(DEFAULT_PERSONAS)
    assert (rule.k, rule.of) == (4, "in_quorum")


def test_default_rule_counts_weights_and_ignores_advisory_personas():
    members = [Persona(f"p{i}", "x", weight=1) for i in range(9)] + [Persona("adv", "x", in_quorum=False)]
    assert QuorumRule.default_for(members).k == 6  # 9 eligible -> ceil(6.0) = 6
    assert QuorumRule.default_for(members, of="all").k == 7  # 10 eligible -> ceil(6.67) = 7


def test_a_huge_k_is_a_clean_error_not_an_overflow():
    with pytest.raises(ValueError):
        QuorumRule(k=10**400)
