"""Small value types shared by the rest of the package."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

VOTE_YES = "YES"
VOTE_NO = "NO"
VOTE_ABSTAIN = "ABSTAIN"
VOTES = (VOTE_YES, VOTE_NO, VOTE_ABSTAIN)

STATUS_OK = "ok"
STATUS_PARSE_FAILURE = "parse_failure"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"
STATUS_DEADLINE = "deadline"
STATUSES = (STATUS_OK, STATUS_PARSE_FAILURE, STATUS_ERROR, STATUS_TIMEOUT, STATUS_DEADLINE)


class Outcome(enum.StrEnum):
    """What a board decides. Only APPROVE means "go ahead".

    UNRECORDED is never produced by a vote: it is the outcome carried by
    ``LedgerWriteError`` when a decision was made but could not be written to
    the ledger, so it must not be acted on.
    """

    APPROVE = "APPROVE"
    VETO = "VETO"
    NO_QUORUM = "NO_QUORUM"
    UNRECORDED = "UNRECORDED"

    @property
    def exit_code(self) -> int:
        return {"APPROVE": 0, "VETO": 1, "NO_QUORUM": 2, "UNRECORDED": 3}[self.value]


def num(x: float) -> int | float:
    """Render whole floats as ints so JSON stays readable (4.0 -> 4)."""
    x = float(x)
    return int(x) if x.is_integer() else round(x, 6)


@dataclass(frozen=True)
class Vote:
    """One persona's *effective* vote.

    ``vote`` is what the board counted. When ``status`` is not ``"ok"`` the
    persona produced no usable answer and ``vote`` is the substitute chosen by
    the board's ``on_error`` policy (``reason`` and ``error`` say why).
    """

    persona: str
    vote: str
    reason: str
    confidence: float | None
    status: str = STATUS_OK
    error: str | None = None
    duration_s: float = 0.0
    weight: float = 1
    in_quorum: bool = True
    hard_veto: bool = False

    @property
    def synthetic(self) -> bool:
        """True when the board substituted this vote (error, timeout, garbage)."""
        return self.status != STATUS_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona": self.persona,
            "vote": self.vote,
            "reason": self.reason,
            "confidence": self.confidence,
            "status": self.status,
            "error": self.error,
            "duration_s": round(self.duration_s, 4),
            "weight": num(self.weight),
            "in_quorum": self.in_quorum,
            "hard_veto": self.hard_veto,
        }


@dataclass(frozen=True)
class Dissent:
    """A persona that voted NO, and why."""

    persona: str
    reason: str
    in_quorum: bool = True
    hard_veto: bool = False
    synthetic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona": self.persona,
            "reason": self.reason,
            "in_quorum": self.in_quorum,
            "hard_veto": self.hard_veto,
            "synthetic": self.synthetic,
        }


@dataclass(frozen=True)
class ParseFailure:
    """A reply that could not be turned into a vote."""

    persona: str
    error: str
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"persona": self.persona, "error": self.error, "excerpt": self.excerpt}


@dataclass(frozen=True)
class ErrorRecord:
    """A persona call that raised, timed out, or was cut off by the deadline."""

    persona: str
    kind: str  # "error" | "timeout" | "deadline"
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"persona": self.persona, "kind": self.kind, "message": self.message}
