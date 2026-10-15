"""Quorum arithmetic.

The rules, in the order they are applied (``Y``, ``N``, ``A`` are the summed
weights of YES / NO / ABSTAIN votes from the *eligible* personas, ``T`` is the
total eligible weight, ``k`` is the threshold):

1. HARD VETO   any persona with ``hard_veto=True`` whose effective vote is NO
               (eligible or not) -> VETO. A NO that the board substituted for a
               failed persona (error, timeout, garbage) only counts here when
               the board's ``on_error`` policy is ``"veto"``.
2. If ``Y >= k``:
       ``Y > N``  -> APPROVE
       ``N > Y``  -> VETO        (the threshold was met but NO outweighs YES)
       ``Y == N`` -> NO_QUORUM   (a tie never approves)
3. Else (``Y < k``):
       ``T < k``     -> NO_QUORUM (misconfiguration: k exceeds the eligible weight;
                                   ``Board`` refuses to be built this way)
       ``T - N < k`` -> VETO     (the NO votes alone make k unreachable)
       otherwise     -> NO_QUORUM (abstentions, errors under on_error="abstain"
                                   or too few YES leave it undecided)

Note the last line: when ``Y < k`` but approval is still arithmetically possible
the outcome is NO_QUORUM even if ``N > Y`` (for example k=2 of 5 with 1 YES,
2 NO, 2 ABSTAIN). VETO is reserved for "approval is impossible" and "k was met
but NO outweighs YES". NO_QUORUM never approves either, so this is the cautious
reading; it only affects the exit code and the label.

"Eligible" means ``in_quorum=True`` personas when ``of="in_quorum"`` (default),
or every persona when ``of="all"``. Votes of non-eligible personas are recorded
and counted in ``Tally.advisory`` but never move Y, N or A.

Comparisons use a 1e-9 tolerance so float weights such as 0.1 + 0.2 behave.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, NamedTuple

from .models import VOTE_NO, VOTE_YES, Outcome, Vote, num
from .textutil import finite_number

OF_CHOICES = ("in_quorum", "all")
EPS = 1e-9
MIN_DEFAULT_K = 4


def default_k(eligible_weight: float) -> float:
    """The default threshold for a board of this size: ``max(4, ceil(2/3 x eligible weight))``.

    Five eligible personas give 4 (4-of-5); ten give 7, so a bigger board does not
    approve on a bare minority-plus-abstentions.
    """
    return max(MIN_DEFAULT_K, math.ceil(2 * eligible_weight / 3 - EPS))


@dataclass(frozen=True)
class QuorumRule:
    """``k`` weighted YES votes (out of the ``of`` personas) are needed to approve."""

    k: float = 4
    of: str = "in_quorum"

    def __post_init__(self) -> None:
        if not finite_number(self.k) or self.k <= 0:
            raise ValueError("quorum k must be a finite number > 0")
        if self.of not in OF_CHOICES:
            raise ValueError(f"quorum 'of' must be one of {OF_CHOICES}, got {self.of!r}")

    @classmethod
    def default_for(cls, members: Iterable[Any], of: str = "in_quorum") -> "QuorumRule":
        """The default rule for these personas: ``k = default_k(eligible weight)``."""
        rule = cls(k=1, of=of)
        return cls(k=default_k(rule.eligible_weight(members)), of=of)

    def eligible(self, member: Any) -> bool:
        """True when a persona / vote (anything with ``in_quorum``) counts towards the tally."""
        return self.of == "all" or bool(member.in_quorum)

    def eligible_weight(self, members: Iterable[Any]) -> float:
        return sum(m.weight for m in members if self.eligible(m))

    def describe(self) -> str:
        return f"{num(self.k)} YES of {self.of} personas (YES must outnumber NO; a hard-veto NO overrides)"

    def to_dict(self) -> dict[str, Any]:
        return {"k": num(self.k), "of": self.of, "describe": self.describe()}


@dataclass(frozen=True)
class Tally:
    yes: float
    no: float
    abstain: float
    eligible_weight: float
    k: float
    eligible: int
    advisory: dict[str, int] = field(default_factory=lambda: {"yes": 0, "no": 0, "abstain": 0})

    def to_dict(self) -> dict[str, Any]:
        return {
            "yes": num(self.yes),
            "no": num(self.no),
            "abstain": num(self.abstain),
            "eligible_weight": num(self.eligible_weight),
            "k": num(self.k),
            "eligible": self.eligible,
            "advisory": dict(self.advisory),
        }


class Verdict(NamedTuple):
    outcome: Outcome
    tally: Tally
    rationale: str


def evaluate(votes: Iterable[Vote], rule: QuorumRule, *, synthetic_hard_veto: bool = True) -> Verdict:
    """Turn effective votes into an outcome. Pure function; see the module docstring.

    ``synthetic_hard_veto``: whether a NO the board substituted for a failed
    persona can trigger that persona's hard veto (the board passes True only for
    ``on_error="veto"``).
    """
    votes = list(votes)
    y = n = a = 0.0
    eligible = 0
    advisory = {"yes": 0, "no": 0, "abstain": 0}
    for v in votes:
        if rule.eligible(v):
            eligible += 1
            if v.vote == VOTE_YES:
                y += v.weight
            elif v.vote == VOTE_NO:
                n += v.weight
            else:
                a += v.weight
        else:
            advisory[v.vote.lower()] += 1
    total = y + n + a
    k = float(rule.k)
    tally = Tally(y, n, a, total, k, eligible, advisory)

    hard = [v.persona for v in votes if v.hard_veto and v.vote == VOTE_NO and (synthetic_hard_veto or not v.synthetic)]
    if hard:
        return Verdict(Outcome.VETO, tally, f"hard veto by {', '.join(hard)}")

    if y + EPS >= k:
        if y > n + EPS:
            return Verdict(Outcome.APPROVE, tally, f"{num(y)} YES >= k={num(k)} and YES outnumber NO ({num(n)})")
        if n > y + EPS:
            return Verdict(Outcome.VETO, tally, f"{num(y)} YES reached k={num(k)} but {num(n)} NO outweigh them")
        return Verdict(Outcome.NO_QUORUM, tally, f"tie: {num(y)} YES vs {num(n)} NO; a tie never approves")

    if total + EPS < k:
        return Verdict(
            Outcome.NO_QUORUM, tally, f"k={num(k)} exceeds the eligible weight {num(total)}; the board cannot approve"
        )
    if total - n + EPS < k:
        return Verdict(
            Outcome.VETO,
            tally,
            f"{num(n)} of {num(total)} weight said NO, so at most {num(total - n)} can still say YES, below k={num(k)}",
        )
    return Verdict(
        Outcome.NO_QUORUM,
        tally,
        f"only {num(y)} YES of the {num(k)} needed; {num(a)} abstained or gave no usable answer"
        + (f" and {num(n)} said NO (approval is still arithmetically possible)" if n > EPS else ""),
    )
