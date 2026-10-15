"""mirofish-lite: a tiny, dependency-free multi-persona veto board.

Before an automated system takes a risky action, a handful of small LLM
"personas" with different risk temperaments each vote YES / NO / ABSTAIN on a
proposal, and a configurable quorum decides. The result is an advisory
ensemble opinion with a recorded rationale. It is not a proof and not a
security boundary; see the README.
"""

__version__ = "0.1.0"

from .board import Board, Decision, LedgerWriteError, canonical_proposal, proposal_hash  # noqa: E402
from .ledger import ArchiveResult, Ledger, LedgerError  # noqa: E402
from .llm import CmdError, CmdLLM, MockLLM, reply  # noqa: E402
from .models import Dissent, ErrorRecord, Outcome, ParseFailure, Vote  # noqa: E402
from .parsing import ParsedVote, VoteParseError, parse_vote  # noqa: E402
from .personas import DEFAULT_PERSONAS, Persona, default_personas  # noqa: E402
from .quorum import QuorumRule, Tally, default_k, evaluate  # noqa: E402

__all__ = [
    "__version__",
    "ArchiveResult",
    "Board",
    "CmdError",
    "CmdLLM",
    "DEFAULT_PERSONAS",
    "Decision",
    "Dissent",
    "ErrorRecord",
    "Ledger",
    "LedgerError",
    "LedgerWriteError",
    "MockLLM",
    "Outcome",
    "ParseFailure",
    "ParsedVote",
    "Persona",
    "QuorumRule",
    "Tally",
    "Vote",
    "VoteParseError",
    "canonical_proposal",
    "default_k",
    "default_personas",
    "evaluate",
    "parse_vote",
    "proposal_hash",
    "reply",
]
