"""Spend gate: ask the board before an automated agent makes a purchase.

Runs fully offline with a scripted MockLLM (a demo stand-in, NOT a review). Note the last step: even when the
board approves, anything over a spending limit still needs a human sign-off.
The board is an ensemble opinion, not the only control on money.

    python examples/spend_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from a checkout without installing

from mirofish_lite import Board, MockLLM, Outcome, Persona, QuorumRule, default_personas, reply  # noqa: E402
from mirofish_lite.report import MOCK_BANNER  # noqa: E402

HUMAN_SIGNOFF_ABOVE = 500  # purchases above this always need a person, whatever the board says

PURCHASES = [
    {
        "item": "12 monthly seats for the team design tool",
        "amount": 120,
        "budget_line": "tools",
        "reversible": "cancel any month",
    },
    {
        "item": "annual licence for the build-cache service",
        "amount": 4800,
        "budget_line": "infrastructure",
        "reversible": "refund within 14 days",
    },
    {
        "item": "40 GPUs on the company card",
        "amount": 120000,
        "budget_line": "none",
        "reversible": "no",
        "quote": "none",
    },
]


def scripted_model() -> MockLLM:
    return MockLLM(
        default=reply("YES", "Cost is bounded, on budget and reversible."),
        rules=[
            (
                lambda persona, system, user: (
                    "40 GPUs" in user and persona in ("cash-flow", "risk-officer", "conservative")
                ),
                reply("NO", "Large, unbudgeted, non-refundable commitment with no quote."),
            )
        ],
    )


def main() -> int:
    print(f"{MOCK_BANNER}: the votes below are scripted for illustration")
    personas = default_personas()
    # Optional: make the risk officer's NO final, whatever the tally says.
    personas = [
        Persona(p.name, p.system_prompt, p.weight, p.in_quorum, hard_veto=(p.name == "risk-officer")) for p in personas
    ]
    board = Board(personas, scripted_model(), quorum=QuorumRule(k=4))

    results = []
    for p in PURCHASES:
        d = board.decide(p)
        results.append(d.outcome)
        line = f"{p['item'][:42]:<42} {p['amount']:>7} -> {d.outcome.value:<9}"
        if d.outcome is Outcome.APPROVE and p["amount"] > HUMAN_SIGNOFF_ABOVE:
            line += "  but over the limit: needs a human sign-off"
        elif d.outcome is Outcome.APPROVE:
            line += "  proceed"
        else:
            line += f"  blocked ({d.rationale})"
        print(line)

    return 0 if results == [Outcome.APPROVE, Outcome.APPROVE, Outcome.VETO] else 1


if __name__ == "__main__":
    sys.exit(main())
