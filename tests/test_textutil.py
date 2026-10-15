import pytest

from mirofish_lite.textutil import clean_text, finite_number, redact_secrets, scrub_error

ESC = chr(27)
BEL = chr(7)


def test_ansi_csi_osc_and_short_sequences_are_removed():
    text = f"{ESC}[2J{ESC}[Hhello {ESC}[1;32mgreen{ESC}[0m {ESC}]0;title{BEL}world {ESC}c{ESC}]8;;http://x{ESC}\\link"
    assert clean_text(text) == "hello green world link"


def test_unterminated_escape_sequences_do_not_survive():
    assert ESC not in clean_text(f"a {ESC}[31")
    assert ESC not in clean_text(f"a {ESC}]0;never terminated")
    assert ESC not in clean_text(f"a {ESC}")


def test_c1_controls_and_eight_bit_csi_are_removed():
    assert clean_text("x" + chr(0x9B) + "31my" + chr(0x80) + chr(0x81) + "z") == "xyz"
    # an 8-bit string-introducer (APC) swallows everything up to its terminator, or to the end if unterminated
    assert clean_text("x" + chr(0x9F) + "hidden payload") == "x"


def test_control_bidi_and_format_characters_are_removed():
    nasty = (
        "a" + chr(0) + "b" + chr(0x202E) + "c" + chr(0x2067) + "d" + chr(0x200B) + "e" + chr(0xFEFF) + "f" + chr(0x7F)
    )
    assert clean_text(nasty) == "abcdef"


def test_whitespace_is_collapsed_to_single_spaces():
    assert clean_text("  a\n\n\tb \r\n c  ") == "a b c"


def test_text_is_truncated_with_an_ellipsis():
    out = clean_text("x" * 500, 50)
    assert len(out) == 50 and out.endswith("...")


def test_non_strings_are_stringified():
    assert clean_text(12) == "12"
    assert clean_text(None) == "None"


def test_ordinary_unicode_survives():
    assert clean_text("café → 中文 \U0001f600") == "café → 中文 \U0001f600"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdef1234567890",
        "sk-proj-abcdefghijklmnopqrstuvwx",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "github_pat_11ABCDEFG0123456789_abcdefghijk",
        "xoxb-1234567890-abcdefghij",
        "AKIAABCDEFGHIJKLMNOP",
        "Bearer abc.def-ghi_jkl",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJl",
        "A" * 40,
    ],
)
def test_credential_looking_strings_are_redacted(secret):
    out = redact_secrets(f"failed calling the API with {secret} as the credential")
    assert secret not in out and "[REDACTED]" in out
    assert "failed calling the API" in out


def test_a_url_password_is_redacted_but_the_host_stays():
    out = redact_secrets("could not reach https://admin:hunter2@models.internal:8443/v1")
    assert "hunter2" not in out and "models.internal" in out


def test_ordinary_words_and_short_tokens_are_left_alone():
    text = "TimeoutError: no reply within 30s from worker-3 (status 502)"
    assert redact_secrets(text) == text


def test_scrub_error_redacts_cleans_and_caps_in_one_go():
    raw = f"boom {ESC}[31m sk-abcdef1234567890 {BEL} " + "z" * 400
    out = scrub_error(raw)
    assert ESC not in out and BEL not in out and "sk-abcdef" not in out
    assert len(out) <= 160
    assert scrub_error(RuntimeError("plain failure")) == "plain failure"


def test_a_secret_cannot_hide_by_being_cut_by_the_cap():
    # the secret straddles the 160-character cut; redaction happens before truncation
    raw = "x " * 76 + "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    assert "ghp_" not in scrub_error(raw)


@pytest.mark.parametrize(
    "value, expected",
    [
        (1, True),
        (0.5, True),
        (10**400, False),
        (float("inf"), False),
        (float("nan"), False),
        (True, False),
        ("1", False),
        (None, False),
    ],
)
def test_finite_number(value, expected):
    assert finite_number(value) is expected
