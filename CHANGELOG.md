# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) (0.x: the API may still change).

## [Unreleased]

### Added

- `templates/weekly_council_report.md`: a placeholder-only template for a weekly review of the board's decisions
  (tally, quoted vetoes, approvals that went wrong, persona health, ledger integrity).

## [0.1.0] - 2026-10-15

First release.

### Pre-publication hardening (behaviour changes after an adversarial review)

Version stays 0.1.0 (never published). These changed what the code does:

- **No silent k clamp.** A board with fewer than 4 eligible weight is refused (exit 3) unless `--k` is given; the CLI
  used to lower k to the board size, so a one-persona board approved on one model.
- **A failing command records only its exit status.** Its stderr (which can echo paths and user names) and the text of
  a start failure no longer reach the votes, `errors[]` or the ledger.
- **`explain --index` always reads the file as a ledger**, so `--index 99` on a one-record ledger exits 3 instead of
  being ignored.
- **POSIX: the command's process group is killed even after the shell has exited**, so a backgrounded grandchild
  cannot outlive the run.
- **`--llm` is required.** The CLI no longer defaults to an all-YES mock, which approved anything. `--llm mock`
  prints `MOCK MODEL - NOT A REVIEW` on stderr every run; the examples print the same line. The model kind
  (`mock` / `cmd` / `callable`, never a command line or key) is recorded in `config.llm` of every decision and
  ledger entry, shown in the summary and in `explain`.
- **Votes must be exactly one JSON object** (optionally in one code fence). Prose around the object, a second or
  truncated object, any other `"vote"` key, and Unicode look-alike vote words (`"yeſ"`) are parse failures
  instead of being guessed at. Vote words are ASCII-only, case-insensitive.
- **Removed `on_error="yes"` / `--on-error yes`** (fail-open: a dead model could approve). Allowed: `veto` (default),
  `no`, `abstain`. `veto` and `no` differ for hard-veto personas: only `veto` lets a *failed* hard-veto persona veto.
  The summary shows how many votes were substituted.
- **`cmd:` processes no longer outlive the run.** `CmdLLM` tracks its processes, the CLI uses 0.9 x `--timeout` for the
  command, the board cancels calls it abandons (timeout or deadline), and an `atexit` hook plus a final sweep kill
  the rest. New: `CmdLLM.cancel()`, `CmdLLM(env=...)`.
- **Ledger appends are cross-process safe** (`fcntl.flock` / `msvcrt.locking` on a `<ledger>.lock` sidecar) and start
  a fresh line after a torn last line. `archive()` takes the same lock.
- **Exit codes:** any error (including a ledger that cannot be written, a broken pipe or an unexpected exception)
  exits 3, never 1 or 2. Output streams use `errors="backslashreplace"`, so a reason with an arrow no longer
  crashes a narrow console after the ledger recorded the decision.
- **Model-written text is sanitised** before it is stored or printed: ANSI/escape sequences, C0/C1 controls, bidi
  and other invisible format characters are removed. Exception text, parse errors and reply excerpts are also capped
  and have credential-looking strings redacted.
- **Default quorum scales:** `k = max(4, ceil(2/3 x eligible weight))` (still 4 of 5 for the default personas).
- **`LedgerWriteError.decision` is unapproved by construction:** outcome `UNRECORDED` (exit code 3), the board's own
  outcome kept in its notes.
- Persona names are matched with `fullmatch` (a trailing newline is rejected); an enormous weight or `k` and a deeply
  nested proposal or personas file are clean errors (exit 3), not tracebacks.
- Documented exactly: NO > YES with `k` still reachable is NO_QUORUM (not VETO); hard-veto behaviour per `on_error`;
  the closing-marker id is a 64-bit prefix, a convenience rather than a guarantee; `CmdLLM` inherits the full
  environment; the ledger lock is advisory.
- CI: least-privilege `permissions`, job timeout, `ruff check`, `ruff format --check`, tests with warnings as errors.
- The README now states that this project is not affiliated with the MiroFish project.

### Added
- `Persona(name, system_prompt, weight=1, in_quorum=True, hard_veto=False)` and six generic default personas
  (conservative, growth, risk-officer, cash-flow, contrarian, newcomer); the contrarian is advisory.
- `Board(personas, llm, quorum=QuorumRule(k=4, of="in_quorum"), on_error="veto")` with a caller-supplied
  `(system_prompt, user_prompt) -> str` callable; the library makes no network calls itself.
- JSON vote protocol with a strict parser (exactly one object, optionally in one code fence; anything else is a
  parse failure, never a YES).
- Fail-closed error handling (`on_error="veto"` default, plus `"no"` and `"abstain"`; no fail-open setting) for
  exceptions, timeouts, deadline cut-offs and unparsable replies.
- Prompt-injection hygiene: proposal passed as data inside delimiters keyed by the proposal hash.
- `Decision` object (outcome APPROVE / VETO / NO_QUORUM, votes, tally, dissent, parse failures, errors, quorum rule,
  proposal hash, timestamp, call count) with `to_dict()`, `to_json()` and `explain()`.
- Weighted quorum maths with documented tie, veto and hard-veto rules.
- Append-only JSONL `Ledger` with opt-in proposal storage and a rotate-then-verify `archive()`.
- Budget controls: `max_personas_parallel`, per-persona `timeout_s`, overall `deadline_s`, `max_prompt_chars`,
  `max_reply_chars`; calls are counted.
- `MockLLM` (scripted, deterministic) and `CmdLLM` (pipe prompts to a local command over stdin).
- CLI: `python -m mirofish_lite {decide,board,explain}` with exit codes 0 / 1 / 2 / 3.
- Examples (`deploy_gate.py`, `spend_gate.py`), pytest suite, GitHub Actions CI (Python 3.11 and 3.12 on Linux and
  Windows).
