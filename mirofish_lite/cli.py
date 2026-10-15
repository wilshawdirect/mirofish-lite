"""Command line interface: ``python -m mirofish_lite {decide,board,explain}``.

Exit codes: 0 approve, 1 veto, 2 no quorum, 3 usage error or any other error
(unreadable input, bad arguments, a ledger that could not be written, an
unexpected exception). A failure never exits 1 or 2, so a caller that checks
"exit code 0 means approved" cannot mistake an error for a verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence, TextIO

from . import __version__
from .board import Board, Decision, LedgerWriteError, canonical_proposal, llm_kind
from .ledger import Ledger
from .llm import CmdLLM, MockLLM
from .personas import Persona, default_personas
from .quorum import OF_CHOICES, QuorumRule, default_k
from .report import MOCK_BANNER, explain_dict
from .textutil import clean_text, scrub_error

EXIT_USAGE = 3
EXIT_EPILOG = "exit codes: 0 approve, 1 veto, 2 no quorum, 3 usage error or any other error"
CMD_TIMEOUT_FRACTION = 0.9  # the command is killed a little before the board gives up on it


class UsageError(Exception):
    """Bad arguments or unreadable input; mapped to exit code 3."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # argparse would exit 2, which means NO_QUORUM here
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="mirofish-lite",
        description="A tiny multi-persona veto board. Advisory: an ensemble opinion, not a proof.",
        epilog=EXIT_EPILOG,
    )
    parser.add_argument("--version", action="version", version=f"mirofish-lite {__version__}")
    sub = parser.add_subparsers(dest="command", parser_class=_Parser)

    d = sub.add_parser("decide", help="put a proposal to the board", epilog=EXIT_EPILOG)
    d.add_argument(
        "proposal", help="proposal file (.json is parsed as JSON, anything else is plain text; '-' reads stdin)"
    )
    d.add_argument(
        "--llm",
        default=None,
        help=(
            "REQUIRED. The model to ask: cmd:<shell command reading stdin>, or a demo stand-in "
            "mock | mock:<persona>=<YES|NO|ABSTAIN|error|garbage>,... (a mock is a scripted rubber stamp, NOT a review)"
        ),
    )
    d.add_argument(
        "--cmd-format", choices=("text", "json"), default="text", help="stdin format for cmd: (default text)"
    )
    d.add_argument("--personas", help="JSON file with a list of persona objects (default: the six built-in personas)")
    d.add_argument(
        "--k", type=float, default=None, help="YES weight needed (default max(4, ceil(2/3 x eligible weight)))"
    )
    d.add_argument("--of", choices=OF_CHOICES, default="in_quorum", help="which personas count towards the quorum")
    d.add_argument(
        "--on-error",
        choices=("veto", "no", "abstain"),
        default="veto",
        help="what a failing persona counts as: veto (NO; a failing hard-veto persona vetoes; default), no (NO only), abstain",
    )
    d.add_argument("--timeout", type=float, default=30.0, help="per-persona timeout in seconds (default 30)")
    d.add_argument("--deadline", type=float, default=120.0, help="overall deadline in seconds (default 120)")
    d.add_argument("--parallel", type=int, default=4, help="max personas called at once (default 4)")
    d.add_argument(
        "--max-prompt-chars",
        type=int,
        default=12_000,
        help="refuse proposals whose rendered prompt is longer (default 12000)",
    )
    d.add_argument("--ledger", help="append the decision to this JSONL ledger")
    d.add_argument(
        "--store-proposal", action="store_true", help="also store the proposal text in the ledger (default: hash only)"
    )
    d.add_argument("--out", help="write the full decision JSON to this file")
    d.add_argument("--json", action="store_true", help="print the full decision JSON instead of a summary")

    b = sub.add_parser("board", help="print the default personas")
    b.add_argument("--full", action="store_true", help="include the full persona prompts")
    b.add_argument("--json", action="store_true", help="print as JSON")

    e = sub.add_parser("explain", help="explain a saved decision (a decision.json, or a ledger .jsonl)")
    e.add_argument("decision", help="decision JSON file or ledger JSONL file")
    e.add_argument(
        "--index", type=int, default=None, help="read the file as a ledger and show this record (default -1, the last)"
    )
    return parser


# -- helpers --------------------------------------------------------------------


def _read_text(path: str) -> str:
    try:
        if path == "-":
            return sys.stdin.read()
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"cannot read {path}: {scrub_error(exc)}") from exc


def _load_proposal(path: str) -> Any:
    text = _read_text(path)
    if path == "-":  # stdin: JSON if it parses, otherwise plain text
        try:
            return json.loads(text)
        except (ValueError, RecursionError):
            return text
    if not path.lower().endswith(".json"):
        return text
    try:
        return json.loads(text)
    except RecursionError:
        raise UsageError(f"{path} is nested too deeply") from None
    except ValueError as exc:
        raise UsageError(f"{path} is not valid JSON: {scrub_error(exc)}") from exc


def _load_personas(path: str | None) -> list[Persona]:
    if path is None:
        return default_personas()
    try:
        raw = json.loads(_read_text(path))
        if not isinstance(raw, list):
            raise ValueError("expected a JSON list of persona objects")
        return [Persona.from_dict(item) for item in raw]
    except (ValueError, TypeError, RecursionError) as exc:
        raise UsageError(f"bad personas file {path}: {scrub_error(exc)}") from exc


def _build_llm(spec: str | None, args: argparse.Namespace) -> Any:
    if spec is None:
        raise UsageError(
            "--llm is required: name the model to ask (cmd:<shell command reading stdin>), "
            "or use mock / mock:<persona>=<VALUE>,... for a scripted demo that is NOT a review"
        )
    if spec == "mock":
        return MockLLM()
    if spec.startswith("mock:"):
        try:
            return MockLLM.from_spec(spec[len("mock:") :])
        except ValueError as exc:
            raise UsageError(scrub_error(exc)) from exc
    if spec.startswith("cmd:"):
        timeout = args.timeout * CMD_TIMEOUT_FRACTION if args.timeout and args.timeout > 0 else args.timeout
        try:
            return CmdLLM(spec[len("cmd:") :], timeout=timeout, input_format=args.cmd_format)
        except ValueError as exc:
            raise UsageError(scrub_error(exc)) from exc
    raise UsageError(f"unknown --llm {clean_text(spec, 60)!r}; use cmd:<command>, mock or mock:<spec>")


def _summary(decision: Decision) -> str:
    t = decision.tally.to_dict()
    lines = [
        f"{decision.outcome.value}  YES {t['yes']}  NO {t['no']}  ABSTAIN {t['abstain']}"
        f"  (need {t['k']})  substituted {decision.substituted}  model {clean_text(decision.llm, 32)}"
        f"  hash {decision.proposal_hash[:12]}",
        f"why: {clean_text(decision.rationale)}",
    ]
    if decision.substituted:
        lines.append(
            f"warning: {decision.substituted} vote(s) were substituted by the board (error / timeout / deadline / unparsable reply)"
        )
    for x in decision.dissent:
        tag = " [board-substituted]" if x.synthetic else ""
        lines.append(f"  NO  {clean_text(x.persona)}{tag}: {clean_text(x.reason)}")
    return "\n".join(lines)


# -- commands -------------------------------------------------------------------


def _cmd_decide(args: argparse.Namespace, out: TextIO) -> int:
    llm = _build_llm(args.llm, args)
    proposal = _load_proposal(args.proposal)
    try:
        canonical_proposal(proposal)
    except (TypeError, ValueError) as exc:
        raise UsageError(f"unusable proposal: {scrub_error(exc)}") from exc
    personas = _load_personas(args.personas)

    try:
        if args.k is None:
            eligible = QuorumRule(k=1, of=args.of).eligible_weight(personas)
            k = default_k(eligible)  # a board with fewer than 4 eligible weight is refused: pass --k
        else:
            k = args.k
        rule = QuorumRule(k=k, of=args.of)
        ledger = Ledger(args.ledger, store_proposal=args.store_proposal) if args.ledger else None
        board = Board(
            personas,
            llm,
            quorum=rule,
            on_error=args.on_error,
            max_personas_parallel=args.parallel,
            timeout_s=args.timeout,
            deadline_s=args.deadline,
            max_prompt_chars=args.max_prompt_chars,
            ledger=ledger,
        )
    except (ValueError, TypeError) as exc:
        raise UsageError(scrub_error(exc)) from exc

    if llm_kind(llm) == "mock":
        print(MOCK_BANNER, file=sys.stderr)
    try:
        decision = board.decide(proposal)
    except LedgerWriteError as exc:
        print(f"mirofish-lite: error: {exc}; treat the action as NOT approved", file=sys.stderr)
        return EXIT_USAGE
    finally:
        cancel = getattr(llm, "cancel", None)
        if callable(cancel):
            cancel()  # no command process outlives the run, whichever way it ends

    if args.out:
        try:
            Path(args.out).write_text(decision.to_json(indent=2), encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"cannot write {args.out}: {scrub_error(exc)}") from exc
    if args.json:
        out.write(decision.to_json(indent=2) + "\n")
    else:
        out.write(_summary(decision) + "\n")
    return decision.exit_code


def _cmd_board(args: argparse.Namespace, out: TextIO) -> int:
    personas = default_personas()
    if args.json:
        out.write(json.dumps([p.to_dict() for p in personas], indent=2) + "\n")
        return 0
    out.write("Default personas (default quorum: 4 YES of the in-quorum personas)\n\n")
    width = max(len(p.name) for p in personas)
    for p in personas:
        flags = ["in quorum" if p.in_quorum else "advisory", f"weight {p.weight}"]
        if p.hard_veto:
            flags.append("hard veto")
        out.write(f"{p.name:<{width}}  [{', '.join(flags)}]\n")
        if args.full:
            out.write(f"    {p.system_prompt}\n\n")
        else:
            parts = p.system_prompt.split(". ")
            out.write(f"    {(parts[1] if len(parts) > 1 else parts[0]).rstrip('.')}.\n")
    return 0


def _cmd_explain(args: argparse.Namespace, out: TextIO) -> int:
    text = _read_text(args.decision)
    record: Any = None
    as_ledger = args.index is not None or str(args.decision).endswith(".jsonl")
    if not as_ledger:
        try:
            record = json.loads(text)
        except (ValueError, RecursionError):
            as_ledger = True
    if as_ledger:
        index = -1 if args.index is None else args.index
        records = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except (ValueError, RecursionError) as exc:
                raise UsageError(f"{args.decision} line {n} is not valid JSON: {scrub_error(exc)}") from exc
        if not records:
            raise UsageError(f"{args.decision} contains no records")
        try:
            record = records[index]
        except IndexError:
            raise UsageError(f"--index {index} is out of range (file has {len(records)} records)") from None
    if not isinstance(record, dict):
        raise UsageError(f"{args.decision} does not contain a decision object")
    try:
        out.write(explain_dict(record) + "\n")
    except ValueError as exc:
        raise UsageError(scrub_error(exc)) from exc
    return 0


def _harden_streams() -> None:
    """A reason containing a character the console cannot encode (an arrow, say) must
    not crash the program after the ledger already recorded the decision."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    _harden_streams()
    out = stdout if stdout is not None else sys.stdout
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    if not args.command:
        parser.print_usage(sys.stderr)
        print("mirofish-lite: error: a command is required (decide, board, explain)", file=sys.stderr)
        return EXIT_USAGE
    handlers = {"decide": _cmd_decide, "board": _cmd_board, "explain": _cmd_explain}
    try:
        return handlers[args.command](args, out)
    except UsageError as exc:
        print(f"mirofish-lite: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - any failure is an error (3), never a verdict (1 or 2)
        try:
            print(f"mirofish-lite: error: unexpected {type(exc).__name__}: {scrub_error(exc)}", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
        return EXIT_USAGE
