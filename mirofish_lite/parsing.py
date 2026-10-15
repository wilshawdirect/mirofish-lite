"""Strict parsing of a persona's reply.

The accepted shape is exactly one JSON object, optionally wrapped in ONE code
fence, with whitespace allowed around it:

    {"vote": "YES", "reason": "...", "confidence": 0.8}

    ```json
    {"vote": "YES", "reason": "...", "confidence": 0.8}
    ```

Everything else is a :class:`VoteParseError`, which the board treats as a failed
vote (counted per its ``on_error`` policy), never as a quiet YES. That includes:

* prose before or after the object, a second object, or a truncated one
  (a model that "thinks aloud" next to a vote, or echoes text from the proposal,
  is rejected rather than guessed at);
* any additional ``"vote"`` key anywhere in the reply, nested or repeated;
* duplicate keys inside the object;
* a vote word that is not exactly YES, NO or ABSTAIN. Matching is
  case-insensitive for ASCII only; there is no Unicode folding, so look-alikes
  such as a dotless or long-s letter are rejected.

Object keys are matched case-insensitively (ASCII). ``reason`` is cleaned
(control characters and ANSI escapes removed, whitespace collapsed, cut to 240
characters). ``confidence`` is clamped to 0..1 (missing means 0.5).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from .models import VOTES
from .textutil import clean_text

MAX_REASON_CHARS = 240
MAX_TEXT_CHARS = 20_000
DEFAULT_CONFIDENCE = 0.5

_ASCII_WS = " \t\r\n"
_FENCE_RE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)\s*```", re.S)
_VOTE_KEY_RE = re.compile(r'"vote"\s*:', re.I)


class VoteParseError(ValueError):
    """The reply could not be turned into a valid vote."""


@dataclass(frozen=True)
class ParsedVote:
    vote: str
    reason: str
    confidence: float


class _Obj(dict):
    """A dict that remembers whether the JSON text repeated a key."""

    dup: bool = False


def _norm_key(key: str) -> str:
    key = key.strip(_ASCII_WS)
    return key.lower() if key.isascii() else key


class _Collector:
    """json object_pairs_hook that also counts how many objects carry a ``vote`` key."""

    def __init__(self) -> None:
        self.vote_objects = 0

    def hook(self, pairs: list[tuple[str, Any]]) -> _Obj:
        out = _Obj()
        for key, value in pairs:
            norm = _norm_key(key)
            if norm in out:
                out.dup = True
            out[norm] = value
        if "vote" in out:
            self.vote_objects += 1
        return out


def _clean_confidence(value: Any) -> float:
    if value is None:
        return DEFAULT_CONFIDENCE
    if isinstance(value, bool):
        raise VoteParseError("confidence must be a number, not a boolean")
    if isinstance(value, str):
        text = value.strip(_ASCII_WS)
        if not text.isascii():
            raise VoteParseError("confidence is not an ASCII number")
        try:
            value = float(text)
        except ValueError:
            raise VoteParseError("confidence is not a number") from None
    if not isinstance(value, (int, float)):
        raise VoteParseError(f"confidence must be a number, got {type(value).__name__}")
    try:
        value = float(value)
    except OverflowError:
        raise VoteParseError("confidence is out of range") from None
    if not math.isfinite(value):
        raise VoteParseError("confidence is not finite")
    return min(1.0, max(0.0, value))


def _from_object(obj: _Obj) -> ParsedVote:
    if obj.dup:
        raise VoteParseError("duplicate keys in vote object")
    raw = obj["vote"]
    if not isinstance(raw, str):
        raise VoteParseError(f"'vote' must be a string, got {type(raw).__name__}")
    word = raw.strip(_ASCII_WS)
    if not word.isascii() or word.upper() not in VOTES:
        raise VoteParseError("'vote' must be exactly YES, NO or ABSTAIN (ASCII)")
    reason = obj.get("reason")
    return ParsedVote(
        vote=word.upper(),
        reason=clean_text("" if reason is None else reason, MAX_REASON_CHARS),
        confidence=_clean_confidence(obj.get("confidence")),
    )


def parse_vote(text: Any) -> ParsedVote:
    """Parse a persona reply. Raises :class:`VoteParseError` on anything but exactly one vote object."""
    if not isinstance(text, str):
        raise VoteParseError(f"reply is not text (got {type(text).__name__})")
    if not text.strip(_ASCII_WS):
        raise VoteParseError("empty reply")
    if len(text) > MAX_TEXT_CHARS:
        raise VoteParseError("reply is too long to be a single vote object")
    if len(_VOTE_KEY_RE.findall(text)) > 1:
        raise VoteParseError("reply contains more than one 'vote' key")

    body = text.strip(_ASCII_WS)
    fence = _FENCE_RE.fullmatch(body)
    if fence:
        body = fence.group(1).strip(_ASCII_WS)

    collector = _Collector()
    try:
        obj = json.loads(body, object_pairs_hook=collector.hook)
    except (ValueError, RecursionError):
        raise VoteParseError(
            "reply is not exactly one JSON object (text around it, a second object, or truncated JSON)"
        ) from None
    if not isinstance(obj, _Obj):
        raise VoteParseError("reply is not a JSON object")
    if collector.vote_objects > 1:
        raise VoteParseError("reply contains conflicting vote objects")
    if "vote" not in obj:
        raise VoteParseError("JSON object has no 'vote' key")
    return _from_object(obj)
