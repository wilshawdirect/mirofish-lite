"""Deploy gate: ask the board before applying a configuration change.

Runs fully offline: the model is a scripted MockLLM (a rubber stamp for the demo, NOT a review). Swap it for any callable
``(system_prompt, user_prompt) -> str`` (for example one that calls a local
model) to use it for real.

    python examples/deploy_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from a checkout without installing

from mirofish_lite import Board, MockLLM, Outcome, reply  # noqa: E402
from mirofish_lite.report import MOCK_BANNER  # noqa: E402

CONFIG_CHANGE = {
    "action": "config_change",
    "service": "image-resizer (staging)",
    "change": "raise cache TTL from 60 s to 300 s",
    "rollback": "revert the flag; takes effect on next request",
    "blast_radius": "staging only, ~40 internal users",
}

DESTRUCTIVE_CHANGE = {
    "action": "migration",
    "service": "orders (production)",
    "change": "DROP TABLE order_history; the nightly backup job is currently failing",
    "rollback": "none",
}


def scripted_model() -> MockLLM:
    """Stand-in for a real model: approves small reversible changes, rejects data loss."""
    return MockLLM(
        responses={
            # The contrarian is advisory (not in the quorum) but its objection is still reported.
            "contrarian": reply("NO", "Nobody has measured stale-read impact at the new TTL; watch the hit rate."),
        },
        default=reply("YES", "Small, reversible, flag-guarded change with a one-step rollback."),
        rules=[
            (
                lambda persona, system, user: "DROP TABLE" in user,
                reply("NO", "Irreversible data loss while the backup is failing; no rollback."),
            )
        ],
    )


def main() -> int:
    print(f"{MOCK_BANNER}: the votes below are scripted for illustration")
    board = Board(llm=scripted_model())  # default six personas, 4-of-5 quorum, fail-closed on errors

    approve = board.decide(CONFIG_CHANGE)
    print(
        f"config change   -> {approve.outcome.value:<9} {approve.tally.to_dict()['yes']} YES / {approve.tally.to_dict()['no']} NO"
        f"   dissent: {[d.persona for d in approve.dissent]}"
    )
    if approve.outcome is Outcome.APPROVE:
        print("                   applying change (the caller acts only on APPROVE)")

    veto = board.decide(DESTRUCTIVE_CHANGE)
    print(
        f"destructive one -> {veto.outcome.value:<9} {veto.tally.to_dict()['yes']} YES / {veto.tally.to_dict()['no']} NO"
        f"   why: {veto.rationale}"
    )
    if veto.outcome is not Outcome.APPROVE:
        print("                   not applying; a human decides what happens next")

    ok = approve.outcome is Outcome.APPROVE and veto.outcome is Outcome.VETO
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
