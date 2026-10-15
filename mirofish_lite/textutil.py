"""Text hygiene for everything that is stored or printed.

Model-written text (reasons, error messages, reply excerpts) is untrusted. It
must not be able to move the cursor, clear the screen, recolour a line to look
like a verdict, flip the text direction, or smuggle a credential into a
ledger.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

# ANSI / VT escape sequences: OSC, DCS-style strings, CSI and short ESC sequences,
# in both the 7-bit (ESC ...) and the 8-bit C1 (0x9b ...) forms.
_ANSI_RE = re.compile(
    r"(?:\x1b\]|\x9d)[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)?"
    r"|(?:\x1b[PX^_]|[\x90\x98\x9e\x9f])[^\x1b\x9c]*(?:\x1b\\|\x9c)?"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]?"
    r"|\x1b[ -/]*[0-~]?"
)
_WS_RE = re.compile(r"\s+")

REDACTED = "[REDACTED]"

_SECRET_PATTERNS = [
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{10,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+){0,2}"),
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+@"),
    re.compile(r"[A-Za-z0-9_+/=-]{32,}"),
]


def finite_number(value: Any) -> bool:
    """True for a real, finite int/float (not a bool). Huge ints (10**400) are not finite floats."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def clean_text(value: Any, limit: int | None = None) -> str:
    """One printable line: ANSI sequences, control characters (C0, C1, DEL), bidi and
    other format characters removed; all whitespace collapsed; optionally truncated."""
    text = value if isinstance(value, str) else str(value)
    text = _ANSI_RE.sub("", text)
    text = _WS_RE.sub(" ", text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = _WS_RE.sub(" ", text).strip()
    if limit is not None and len(text) > limit:
        text = text[: max(limit - 3, 0)].rstrip() + "..."
    return text


def redact_secrets(text: str) -> str:
    """Replace credential-looking strings (sk-, ghp_, Bearer, JWTs, URL passwords and any
    long token-like run) with ``[REDACTED]``."""
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("(?<=://)"):
            text = pattern.sub(REDACTED + "@", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


def scrub_error(value: Any, limit: int = 160) -> str:
    """Exception text made fit to store: secrets redacted, control characters removed, length capped."""
    text = value if isinstance(value, str) else str(value)
    text = redact_secrets(_ANSI_RE.sub("", text))
    return clean_text(text, limit)
