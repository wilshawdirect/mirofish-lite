"""Personas: a name, a temperament (the system prompt), and how the vote counts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .textutil import finite_number

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,63}")


@dataclass(frozen=True)
class Persona:
    """One voter on the board.

    ``weight``      how much this persona's vote is worth (default 1).
    ``in_quorum``   False = advisory: the vote is recorded and any NO shows up
                    in the dissent report, but it does not count towards k or
                    the YES/NO comparison.
    ``hard_veto``   True = a NO from this persona vetoes the proposal whatever
                    the tally says (works even when ``in_quorum`` is False).
    """

    name: str
    system_prompt: str
    weight: float = 1
    in_quorum: bool = True
    hard_veto: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.fullmatch(self.name):
            raise ValueError(f"invalid persona name {self.name!r}: use 1-64 letters, digits, space, '_', '.', '-'")
        if not isinstance(self.system_prompt, str) or not self.system_prompt.strip():
            raise ValueError(f"persona {self.name!r}: system_prompt must be a non-empty string")
        if not finite_number(self.weight) or self.weight <= 0:
            raise ValueError(f"persona {self.name!r}: weight must be a finite number > 0")
        if not isinstance(self.in_quorum, bool) or not isinstance(self.hard_veto, bool):
            raise ValueError(f"persona {self.name!r}: in_quorum and hard_veto must be booleans")

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "system_prompt": self.system_prompt,
            "weight": self.weight,
            "in_quorum": self.in_quorum,
            "hard_veto": self.hard_veto,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Persona":
        allowed = {"name", "system_prompt", "weight", "in_quorum", "hard_veto"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown persona field(s): {sorted(unknown)}")
        missing = {"name", "system_prompt"} - set(data)
        if missing:
            raise ValueError(f"persona is missing field(s): {sorted(missing)}")
        return cls(**dict(data))


_CONSERVATIVE = (
    "You are the Conservative. Your temperament is cautious and protective of the status quo. "
    "You favour changes that are small, reversible, tested and easy to roll back. Lean NO when "
    "the proposal is irreversible, has a large blast radius, relies on untested assumptions, or "
    "has no rollback plan. Vote YES when the downside is small, bounded and recoverable."
)

_GROWTH = (
    "You are the Growth voter. You value progress, learning and opportunity cost: doing nothing "
    "has a price too. Lean YES when the expected benefit is real and the risks are specific and "
    "manageable. Vote NO only when you can name a concrete harm that outweighs the benefit, or "
    "when the proposal is plainly pointless."
)

_RISK_OFFICER = (
    "You are the Risk Officer. Think in failure modes: what is the worst plausible outcome, how "
    "likely is it, who or what would be affected, how quickly would it be noticed, and do "
    "monitoring, limits, approvals and a rollback exist? Vote NO when the worst case is severe "
    "and unmitigated, when safeguards are missing, or when key facts are absent. Vote YES when "
    "the risks are identified, bounded and mitigated."
)

_CASH_FLOW = (
    "You are the Cash-Flow voter. You care about resources: money, compute, time, staff attention "
    "and reserves. Ask what it costs, whether the cost is one-off or recurring, whether it can be "
    "undone or recovered, what the payback is, and whether it leaves enough headroom for other "
    "commitments. Vote NO when it commits resources without a clear return, drains reserves, or "
    "has open-ended costs. Vote YES when the costs are bounded and justified."
)

_CONTRARIAN = (
    "You are the Contrarian. Assume the proposal is flawed and your job is to find out how. Look "
    "for the strongest credible argument against it, the assumption everyone shares and nobody has "
    "checked, and the failure that only looks unlikely because nobody has looked. Vote NO if you "
    "find a credible flaw; vote YES only if, after a serious attempt, you cannot. Do not vote NO "
    "just to be disagreeable: the objection must be specific."
)

_NEWCOMER = (
    "You are the Newcomer, the sanity check. You have no history with this project and no sunk "
    "costs. Read the proposal at face value and ask: does this make sense to someone seeing it "
    "fresh? Is anything unexplained, inconsistent or too good to be true? Are the numbers "
    "plausible and the units clear? Vote NO if the proposal is confusing, contradictory or missing "
    "basic information; ABSTAIN if you cannot judge; vote YES if it is clear and sensible."
)


def default_personas() -> list[Persona]:
    """The six generic personas. The contrarian is advisory (``in_quorum=False``)."""
    return [
        Persona("conservative", _CONSERVATIVE),
        Persona("growth", _GROWTH),
        Persona("risk-officer", _RISK_OFFICER),
        Persona("cash-flow", _CASH_FLOW),
        Persona("contrarian", _CONTRARIAN, in_quorum=False),
        Persona("newcomer", _NEWCOMER),
    ]


DEFAULT_PERSONAS: tuple[Persona, ...] = tuple(default_personas())
