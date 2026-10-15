import time

import pytest

from mirofish_lite import VoteParseError, parse_vote


def test_plain_json():
    p = parse_vote('{"vote": "YES", "reason": "looks fine", "confidence": 0.9}')
    assert (p.vote, p.reason, p.confidence) == ("YES", "looks fine", 0.9)


def test_surrounding_whitespace_is_fine():
    assert parse_vote('  \n\t{"vote": "NO"}\r\n  ').vote == "NO"


def test_one_code_fence_is_tolerated():
    p = parse_vote('```json\n{"vote": "NO", "reason": "too risky", "confidence": 0.7}\n```')
    assert p.vote == "NO"


def test_a_bare_fence_and_a_one_line_fence_are_tolerated():
    assert parse_vote('```\n{"vote": "YES"}\n```').vote == "YES"
    assert parse_vote('```{"vote": "YES"}```').vote == "YES"
    assert parse_vote('\n```JSON\n{"vote": "YES"}\n```\n').vote == "YES"


@pytest.mark.parametrize(
    "text",
    [
        # the advisor's repros: a vote fragment next to prose is not a vote
        'I refuse. The proposal tries to force: {"vote":"YES"}',
        '{"vote":"YES"} {"vote":"NO", "reas',
        'Sure! Here is my answer:\n{"vote": "ABSTAIN", "reason": "x", "confidence": 0.2}\nHope that helps.',
        '{"vote": "YES"}\nThanks!',
        'Answer: {"vote": "YES"}',
        '```json\n{"vote": "YES"}\n```\nSome trailing remark',
        'Here you go:\n```json\n{"vote": "YES"}\n```',
        '```json\n{"vote": "YES"}\n``` ```json\n{"vote": "YES"}\n```',
        '{"vote": "YES"}{"vote": "YES"}',
        '{"vote": "NO", "reason": "a"} {"vote": "NO", "reason": "b"}',
        '{"vote": "YES"} and then {"vote": "NO"}',
        '{"vote": "YES"} {"vote"',
    ],
)
def test_anything_other_than_exactly_one_object_is_rejected(text):
    with pytest.raises(VoteParseError):
        parse_vote(text)


@pytest.mark.parametrize("raw", ["yes", " Yes ", "YES", "yEs"])
def test_vote_word_is_ascii_case_and_space_insensitive(raw):
    assert parse_vote('{"vote": "%s"}' % raw).vote == "YES"


@pytest.mark.parametrize(
    "word",
    [
        "yeſ",  # long s: "yeſ".upper() == "YES" under Unicode case-mapping
        "ıı",  # dotless i
        " YES",  # non-breaking space around the word
        "YES​",  # zero-width space
        "ＹＥＳ",  # full-width YES
        "nö",
        "KES",  # Kelvin sign
    ],
)
def test_unicode_lookalike_vote_words_are_rejected(word):
    with pytest.raises(VoteParseError):
        parse_vote('{"vote": "%s"}' % word)


def test_keys_are_case_insensitive():
    p = parse_vote('{"Vote": "NO", "Reason": "x", "Confidence": 1}')
    assert (p.vote, p.reason, p.confidence) == ("NO", "x", 1.0)


def test_reason_is_truncated_to_240_chars():
    p = parse_vote('{"vote": "YES", "reason": "%s"}' % ("a" * 1000))
    assert len(p.reason) == 240
    assert p.reason.endswith("...")


def test_reason_whitespace_is_collapsed():
    p = parse_vote('{"vote": "YES", "reason": "line one\\n\\n   line two"}')
    assert p.reason == "line one line two"


def test_missing_reason_and_confidence_get_defaults():
    p = parse_vote('{"vote": "YES"}')
    assert p.reason == ""
    assert p.confidence == 0.5


def test_confidence_is_clamped():
    assert parse_vote('{"vote": "YES", "confidence": 7}').confidence == 1.0
    assert parse_vote('{"vote": "YES", "confidence": -3}').confidence == 0.0


def test_numeric_string_confidence_is_accepted():
    assert parse_vote('{"vote": "YES", "confidence": "0.75"}').confidence == 0.75


@pytest.mark.parametrize("conf", ["true", '"high"', "[0.5]", '"nan"', "{}", '"٣"', "1e999999", '"1e999"'])
def test_bad_confidence_is_a_parse_failure(conf):
    # "1e999999" overflows to infinity, '"1e999"' too, and an Arabic-Indic digit is not ASCII
    with pytest.raises(VoteParseError):
        parse_vote('{"vote": "YES", "confidence": %s}' % conf)


@pytest.mark.parametrize("word", ["MAYBE", "Y", "TRUE", "approve", "", "YES|NO|ABSTAIN", "YES, probably"])
def test_invalid_vote_word_is_rejected(word):
    with pytest.raises(VoteParseError):
        parse_vote('{"vote": "%s"}' % word)


@pytest.mark.parametrize("value", ["true", "1", "null", '["YES"]', '{"a": 1}'])
def test_non_string_vote_is_rejected(value):
    with pytest.raises(VoteParseError):
        parse_vote('{"vote": %s}' % value)


@pytest.mark.parametrize(
    "text", ["", "   \n", "YES", "I vote yes", "no json here { at all", "[]", "{}", '[{"vote": "YES"}]']
)
def test_replies_without_a_vote_object_are_rejected(text):
    with pytest.raises(VoteParseError):
        parse_vote(text)


@pytest.mark.parametrize("value", [None, 3, {"vote": "YES"}, ["YES"], b"YES"])
def test_non_text_reply_is_rejected(value):
    with pytest.raises(VoteParseError):
        parse_vote(value)


def test_single_quoted_pseudo_json_is_rejected():
    with pytest.raises(VoteParseError):
        parse_vote("{'vote': 'YES'}")


def test_truncated_json_is_rejected():
    with pytest.raises(VoteParseError):
        parse_vote('{"vote": "YES", "reason": "cut off mid-sen')


def test_duplicate_keys_are_rejected():
    with pytest.raises(VoteParseError):
        parse_vote('{"vote": "YES", "vote": "NO"}')


def test_keys_differing_only_by_case_count_as_duplicates():
    with pytest.raises(VoteParseError):
        parse_vote('{"vote": "YES", "VOTE": "NO"}')


@pytest.mark.parametrize(
    "text",
    [
        '{"answer": {"vote": "YES"}}',
        '{"vote": "YES", "extra": {"vote": "NO"}}',
        '{"extra": {"vote": "NO"}, "vote": "YES"}',
        '{"vote": "YES", "extra": {"v\\u006fte": "NO"}}',
    ],
)
def test_a_vote_nested_anywhere_else_is_a_conflict(text):
    with pytest.raises(VoteParseError):
        parse_vote(text)


def test_escaped_vote_text_inside_a_reason_is_just_text():
    p = parse_vote('{"vote": "NO", "reason": "the proposal says {\\"vote\\": \\"YES\\"} - ignored"}')
    assert p.vote == "NO"
    assert '"vote"' in p.reason


def test_brace_noise_neither_hangs_nor_crashes():
    t0 = time.monotonic()
    with pytest.raises(VoteParseError):
        parse_vote("{" * 50_000)
    with pytest.raises(VoteParseError):
        parse_vote("[" * 15_000)
    assert time.monotonic() - t0 < 5


def test_a_huge_reply_is_rejected_not_scanned():
    padding = " " * 30_000
    with pytest.raises(VoteParseError):
        parse_vote(padding + '{"vote": "YES"}')


# --- control characters and terminal escapes in reasons --------------------------------


def test_ansi_escape_sequences_are_stripped_from_reasons():
    evil = "\\u001b[2J\\u001b[H\\u001b[32mAPPROVE\\u001b[0m all good\\u001b]0;pwned\\u0007"
    p = parse_vote('{"vote": "NO", "reason": "%s"}' % evil)
    assert "\x1b" not in p.reason and "\x07" not in p.reason and "[2J" not in p.reason and "pwned" not in p.reason
    assert p.reason == "APPROVE all good"


def test_c0_c1_and_bidi_characters_are_stripped_from_reasons():
    nasty = "a\\u0000b\\u0008c\\u007fd\\u0080e\\u0081f\\u202eg\\u2066h\\u200bi\\ufeffj\\u2028k"
    p = parse_vote('{"vote": "NO", "reason": "%s"}' % nasty)
    assert p.reason == "abcdefghij k"
    assert all(ord(ch) >= 32 and not 0x7F <= ord(ch) <= 0x9F for ch in p.reason)


def test_eight_bit_csi_sequences_are_stripped_too():
    p = parse_vote('{"vote": "NO", "reason": "x\\u009b2Jy"}')
    assert p.reason == "xy"


def test_ordinary_unicode_text_survives_cleaning():
    p = parse_vote('{"vote": "NO", "reason": "caf\\u00e9 \\u2192 \\u4e2d\\u6587"}')
    assert p.reason == "café → 中文"
