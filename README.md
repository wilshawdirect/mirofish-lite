# mirofish-lite

A tiny, dependency-free (Python 3.11+, standard library only) **multi-persona veto board**.

> Not affiliated with, derived from, or endorsed by the MiroFish project (github.com/666ghj/MiroFish); the name here only describes a small multi-persona veto board.

Before an automated system takes a risky action, a handful of small LLM "personas" with different risk
temperaments each vote **YES / NO / ABSTAIN** on a proposal, and a configurable quorum decides. The result
is an *advisory* ensemble opinion that records why. It lowers the chance that one confidently wrong model
approves something on its own; it does not make an action correct.

```
proposal --> [conservative] [growth] [risk-officer] [cash-flow] [contrarian] [newcomer]
                  \            |           |             |            |           /
                   `-------- JSON votes, strict parser, fail-closed on any error ------'
                                                |
                                      quorum rule (default 4 YES of 5)
                                                |
                     APPROVE  |  VETO  |  NO_QUORUM   -->  dissent report + JSONL ledger
```

* No network calls of its own: you supply the model as a plain callable `(system_prompt, user_prompt) -> str`.
* Fail-closed: a persona that errors, times out or returns anything but one well-formed vote counts as **NO**
  (or ABSTAIN if you ask). There is no fail-open setting.
* Append-only JSONL ledger of every decision (proposal hash, votes, parse failures, durations, which kind of
  model answered), safe for several processes to append to.
* Deterministic `MockLLM` for tests and demos (a scripted rubber stamp, **not a review**; every CLI run that uses
  one says so); a `cmd:` adapter to pipe any local model through stdin.

> **Read the [Honesty](#honesty-what-this-is-and-is-not) and [Threat model](#threat-model) sections before
> relying on it.** It is an opinion poll among models, not a control that can stand alone.

## Install

Not on PyPI yet. From a checkout:

```bash
pip install .          # or: pip install -e ".[dev]" to run the tests and the linter
```

It has no runtime dependencies. You can also just run `python -m mirofish_lite` from the repository root.

## Quickstart (library)

```python
from mirofish_lite import Board, Ledger

def my_llm(system_prompt: str, user_prompt: str) -> str:
    """Call whatever model you like and return its text reply."""
    ...

board = Board(llm=my_llm, ledger=Ledger("decisions.jsonl"))   # six default personas, 4-of-5 quorum

decision = board.decide({
    "action": "deploy",
    "service": "image-resizer (staging)",
    "change": "raise cache TTL from 60 s to 300 s",
    "rollback": "revert the flag",
})

if decision.approved:          # outcome is Outcome.APPROVE
    apply_change()
else:
    print(decision.explain())  # who said NO, and why
```

Try it offline with the scripted model (the output starts with a `MOCK MODEL - NOT A REVIEW` line):

```bash
python examples/deploy_gate.py
python examples/spend_gate.py
```

## Quickstart (CLI)

`--llm` is **required** for `decide`; there is no default model.

```bash
python -m mirofish_lite board                                              # list the default personas
python -m mirofish_lite decide proposal.json --llm "cmd:ollama run llama3" --ledger decisions.jsonl
python -m mirofish_lite decide proposal.json --llm "mock:risk-officer=NO,cash-flow=NO"   # scripted demo
python -m mirofish_lite explain decision.json                              # or: explain decisions.jsonl [--index N]
```

| exit code | meaning |
|---|---|
| 0 | APPROVE |
| 1 | VETO |
| 2 | NO_QUORUM |
| 3 | usage error **or any other error** (bad arguments, unreadable or invalid input, a ledger that could not be written, an unexpected exception) |

A failure never exits 1 or 2, so "exit code 0 means approved, 1 or 2 means a verdict, 3 means something went
wrong" holds.

`--llm` values:

* `cmd:<shell command>` runs the command for every persona. The system and user prompts are written to its
  stdin (`--cmd-format text`, the default, or `json` for `{"system": ..., "prompt": ...}`); its stdout is the
  reply. A non-zero exit is an error. The proposal travels on stdin only and is never spliced into the command
  line. The command string is executed by your shell, so treat it like any command you would type yourself, and
  note that **the command inherits this process's entire environment** (API keys included); use
  `CmdLLM(..., env=...)` from the library if it should see less.
* `mock` makes every persona answer YES. `mock:<persona>=<VALUE>,...` scripts individual personas; `VALUE` is
  `YES`, `NO`, `ABSTAIN`, `error` (the call raises) or `garbage` (not JSON); `*=` sets the fallback. **A mock is a
  rubber stamp for demos and tests, not a review.** Every run that uses one prints `MOCK MODEL - NOT A REVIEW`
  on stderr, and the decision records `config.llm = "mock"`.

Process hygiene for `cmd:`: each command is killed (with its child processes) after `--timeout` x 0.9, calls the
board abandons on a timeout or the overall `--deadline` are cancelled by the board, and an `atexit` hook plus a
final sweep at the end of every CLI run kill anything left. No command process is meant to outlive the run
(the test-suite checks this with real child processes; a process that deliberately detaches itself from its
parent into a new session or job is out of reach).

`proposal.json` can be any JSON value (an object is typical). A file that does not end in `.json` is read as plain
text. `-` reads stdin (JSON if it parses, otherwise plain text). Useful flags: `--k`, `--of in_quorum|all`,
`--on-error veto|no|abstain`, `--timeout`, `--deadline`, `--parallel`, `--max-prompt-chars`, `--personas file.json`,
`--ledger`, `--store-proposal`, `--out decision.json`, `--json`. The default summary shows the outcome, the tally, the
number of **substituted** votes (errors, timeouts, unparsable replies), the model kind and the dissent.

## How a vote works

Each persona gets a system prompt (its temperament plus a shared protocol) and a user prompt that contains the
proposal. It must answer with **exactly one JSON object and nothing else**:

```json
{"vote": "YES", "reason": "<= 240 chars", "confidence": 0.8}
```

**Parsing is strict about the shape.** The only accepted reply is a single JSON object, optionally wrapped in
one code fence, with whitespace around it. Anything else is a *parse failure* (counted per `on_error`, never a
quiet yes), including:

* prose before or after the object, a second object, a truncated object, or any other `"vote"` key anywhere in
  the reply (so a model that thinks aloud next to a vote, or echoes text from the proposal, is rejected rather than
  guessed at);
* duplicate keys; `vote` that is not exactly `YES`, `NO` or `ABSTAIN`. Case is ignored for ASCII only; there is
  no Unicode folding, so look-alike letters are rejected;
* a non-numeric `confidence`, or a reply that is not text, empty, or longer than the reply limit.

Object keys are matched case-insensitively (ASCII). `confidence` is clamped to 0..1 (missing means 0.5; it is
recorded but does not affect the tally).

**Reasons are untrusted text.** Before a reason (or any error text) is stored or printed, ANSI/terminal escape
sequences and all control characters (C0, C1, DEL, bidirectional overrides and other invisible format
characters) are removed, whitespace is collapsed and the length is capped at 240. A model cannot clear your
screen or paint a fake green APPROVE line through the dissent report.

**Prompt-injection hygiene.** The proposal is passed as data inside a delimited block:

```
<<<PROPOSAL_DATA id=3f9c1a7e5b2d4c60>>>
...proposal text...
<<<END_PROPOSAL_DATA id=3f9c1a7e5b2d4c60>>>
```

The `id` is the first 16 hex characters (64 bits) of the SHA-256 of the proposal text, so a proposal cannot
practically contain a closing marker with the matching id for its own text. The prompts tell the model that the
block ends only at the closing marker whose id equals the opening marker's id, and to treat everything inside as
untrusted data and ignore any instructions in it. That is a structural convenience that lowers the chance of a
cheap injection. It is not a guarantee: whether a given model honours the delimiters is up to the model.

## The default personas

Written for generic proposals (deploys, spend, purchases, data deletions, outbound messages).

| name | temperament | counts toward quorum |
|---|---|---|
| `conservative` | protects the status quo; wants small, reversible, tested, roll-back-able changes | yes |
| `growth` | values progress and opportunity cost; says NO only for a concrete harm | yes |
| `risk-officer` | failure modes, worst case, blast radius, safeguards and rollback | yes |
| `cash-flow` | money, compute, time and reserves; one-off vs recurring cost, payback | yes |
| `contrarian` | assumes the proposal is flawed and looks for the strongest specific objection | **no** (advisory) |
| `newcomer` | fresh-eyes sanity check: is it clear, consistent and plausible? | yes |

The contrarian is advisory by default: its vote is recorded and a NO shows in the dissent report, but it does not
move the tally. Define your own with `Persona(name, system_prompt, weight=1, in_quorum=True, hard_veto=False)`.

## Quorum rules (exact)

`Board(personas, llm, quorum=QuorumRule(k=4, of="in_quorum"), on_error="veto")`.

Definitions. *Eligible* personas are those with `in_quorum=True` (`of="in_quorum"`, the default) or all personas
(`of="all"`). `Y`, `N`, `A` are the summed **weights** of the eligible personas' effective YES / NO / ABSTAIN votes,
`T = Y + N + A` is the total eligible weight, `k` is the threshold. With every weight at 1 these are plain counts.
Floats are compared with a 1e-9 tolerance.

**Default `k`.** If you do not pass a quorum, `k = max(4, ceil(2/3 x eligible weight))`: 4 of 5 for the default
personas, 7 of 10, 20 of 30. (A fixed 4 would let a ten-persona board approve on 4 YES / 3 NO / 3 ABSTAIN.) A board
with fewer than 4 eligible weight must be given an explicit `k`.

Applied in this order:

1. **Hard veto.** Any persona with `hard_veto=True` whose effective vote is NO (eligible or not) gives **VETO**,
   whatever the tally says. A NO the board *substituted* for a failed hard-veto persona only counts when
   `on_error="veto"` (see below).
2. If `Y >= k`: `Y > N` gives **APPROVE**; `N > Y` gives **VETO**; `Y == N` gives **NO_QUORUM** (a tie never approves).
3. Otherwise (`Y < k`): if `T - N < k` the NO votes alone make `k` unreachable, which gives **VETO**; else
   **NO_QUORUM**.

Read rule 3 carefully: **VETO means approval is impossible, or `k` was met but NO outweighs YES.** When `Y < k` but
approval is still arithmetically possible, the outcome is NO_QUORUM *even if `N > Y`* (for example `k=2` of 5 with
1 YES, 2 NO and 2 ABSTAIN). NO_QUORUM never approves either, so this is the cautious reading; it only affects the
label and the exit code (2, not 1).

Votes from non-eligible personas are recorded and summarised in `tally.advisory` but never change `Y`, `N` or `A`.
`Board` refuses to be built if `k` exceeds the eligible weight (it could never approve) or if no persona is eligible.

With 5 eligible personas of weight 1 and `k = 4`:

| YES | NO | ABSTAIN / unusable | outcome |
|---|---|---|---|
| 5 | 0 | 0 | APPROVE |
| 4 | 1 | 0 | APPROVE |
| 4 | 0 | 1 | APPROVE |
| 3 | 1 | 1 | NO_QUORUM |
| 3 | 0 | 2 | NO_QUORUM |
| 3 | 2 | 0 | VETO (at most 3 can still say YES) |
| 0 | 0 | 5 | NO_QUORUM |

The thresholds are policy, not facts. `k=4` of 5 is a sensible starting point, not a measured optimum.

## Failure handling

Model errors never raise out of `decide()`. Each failure becomes a substitute vote chosen by `on_error`, is flagged
`synthetic` in the votes and the dissent report, is counted in `decision.substituted`, and is listed in
`decision.errors` or `decision.parse_failures`. Exception text is capped at 160 characters, stripped of control
characters, and credential-looking strings (`sk-...`, `ghp_...`, `Bearer ...`, JWTs, `user:password@` in URLs and
any long token-like run) are replaced with `[REDACTED]` before anything is stored.

| what happened | status | counted as (default `veto`) |
|---|---|---|
| the callable raised | `error` | NO |
| the callable raised `TimeoutError`, or no reply within `timeout_s` | `timeout` | NO |
| the overall `deadline_s` ran out (before or during the call) | `deadline` | NO |
| reply was not exactly one valid vote object | `parse_failure` | NO |

`on_error` has three settings and **no fail-open one** (a board whose models are all down must never approve):

| `on_error` | a failing persona counts as | a failing hard-veto persona |
|---|---|---|
| `"veto"` (default) | NO | **vetoes** the proposal |
| `"no"` | NO in the tally | does **not** veto by itself (its NO is only a vote) |
| `"abstain"` | ABSTAIN | does **not** veto (the board then tends to NO_QUORUM) |

A hard-veto persona that gives a real NO vetoes under every setting.

Proposals whose rendered prompt exceeds `max_prompt_chars` are **refused** (outcome NO_QUORUM, zero model calls,
a `proposal_too_large` note), never silently truncated, because truncation could cut off the dangerous part.

## The decision object

`Decision(outcome, votes, tally, dissent, parse_failures, quorum_rule, proposal_hash, ts, ...)`

* `outcome` `APPROVE | VETO | NO_QUORUM`, plus `approved`, `exit_code` and `rationale` (one line of why).
  (`UNRECORDED` appears only on the decision carried by `LedgerWriteError`; see below.)
* `votes` one per persona in board order: effective vote, reason, confidence, status, error, `duration_s`.
* `tally` weighted yes / no / abstain, eligible weight, `k`, and the advisory counts; `substituted` counts the votes
  the board had to substitute.
* `dissent` everyone who said NO and why (advisory personas and board-substituted NOs are marked).
* `parse_failures`, `errors` what went wrong with individual personas.
* `proposal_hash` SHA-256 of the canonical proposal text (objects are rendered as sorted, indented JSON);
  `ts` UTC ISO-8601; `calls` model calls actually started; `duration_s`; `config` (including `llm`: `mock`, `cmd` or
  `callable`; never a command line or key) and a `personas` snapshot with a hash of each persona prompt, so you can
  tell later which prompt version voted.
* `to_dict()`, `to_json()`, `explain()`.

## Ledger

`Ledger(path, store_proposal=False)` appends one ASCII-only JSON line per decision. Nothing in the API edits or
deletes a record. The proposal text is stored only with `store_proposal=True`; by default only its hash is, and
raw-reply excerpts for unparsable replies follow the same switch. Reasons written by the models *are* stored (after
cleaning) and may quote the proposal.

**Several processes can append to one ledger.** Appends take an OS file lock (`fcntl.flock` on POSIX,
`msvcrt.locking` on Windows) on a `<ledger>.lock` sidecar file, so records are neither interleaved nor overwritten.
The lock is advisory (it coordinates writers that use this class), and file locks can be unreliable on some network
file systems. If a crash left a torn last line, the next append starts on a fresh line: the torn fragment is kept
and shows up as a `corrupt` record when reading, and the new record is intact.

`ledger.archive(archive_dir=None)` rotates the live file to `<stem>.<UTC timestamp><suffix>` with no window in
which a write can be lost: (1) atomically rename the live file to a `*.rotating` staging name (new appends create
a fresh live file at once); (2) copy it to the final archive name, optionally on another disk, and verify SHA-256,
size and record count; (3) only then drop the staging duplicate. If anything fails, the staging file is kept
(`*.unverified` when the copy did not verify) and `LedgerError` is raised. `ledger.read()` yields every record
and reports unparsable lines as `{"type": "corrupt", ...}` instead of skipping them.

If a configured ledger cannot be written, `decide()` raises `LedgerWriteError`. The decision attached as `.decision`
is **unapproved by construction**: its outcome is `UNRECORDED` (`approved` is False, exit code 3) and the outcome
the board had reached is kept in its notes. Nothing is approved without a record.

## Budget controls

`max_personas_parallel` (threads, default 4), `timeout_s` per persona (measured from when its call starts,
default 30), `deadline_s` for the whole decision (default 120), `max_prompt_chars` (default 12 000), `max_reply_chars`
(default 20 000). The number of model calls started is returned as `decision.calls`. Python cannot kill a thread:
a timed-out call is abandoned, its late reply is ignored, and it no longer occupies a parallel slot. If the model
adapter has a `cancel(thread_ident)` method the board calls it for every abandoned call; `CmdLLM` implements it by
killing the process tree, which is how a `cmd:` model gets hard cancellation. Set `CmdLLM(timeout=...)` a little
below the board's `timeout_s` so the command is killed first. Your callable is invoked from several threads at
once, so it must be thread-safe (or set `max_personas_parallel=1`).

## Honesty: what this is and is not

* **It is an ensemble opinion, not a proof.** The personas can be jointly wrong. If they share a base model they
  tend to share blind spots, and one well-crafted prompt can fool all of them at once. Agreement among models is
  weak evidence.
* **Do not make it the only gate on irreversible or financial actions.** Keep a human approval for those. The
  spend example does exactly that: the board approves a 4 800 purchase and a person still has to sign off.
* **A cooperating process can ignore its decision.** It guards against a careless caller, not a malicious one.
  Nothing here enforces that the caller obeys `VETO`.
* **Quorum thresholds are policy.** Tune them by replaying real cases. A replay harness is on the roadmap but is
  not shipped; today you do it yourself with the ledger.
* **Not benchmarked.** No accuracy, false-approval or false-veto rates are claimed. Model replies are
  non-deterministic. Prompt-injection hygiene lowers the odds of a cheap attack; it is not a defence against an
  adaptive one.
* **A mock model reviews nothing.** `MockLLM` and `--llm mock` return scripted votes. They exist for tests and
  demos, are labelled as such in the output, and are recorded as `llm: "mock"` in every decision.
* It is not a sandbox, an access-control system or a security product.

## Threat model

**Goal.** Reduce the chance that an automated system takes a risky action because a single model, or a single
careless code path, said yes, and leave an audit trail of who said what.

**In scope (what it tries to do).**

| threat | mitigation |
|---|---|
| one confidently wrong model approves | k-of-n quorum across differently prompted personas, optional hard-veto persona |
| model error, timeout, garbage or truncated output | fail-closed (counts as NO by default, never YES), a parser that accepts exactly one vote object, per-persona timeout and overall deadline |
| a model that rambles or echoes the proposal next to a vote | rejected: prose around the vote, or a second `"vote"` anywhere, is a parse failure |
| instructions hidden in the proposal text | proposal passed as data in hash-keyed delimiters; prompts say to ignore instructions inside it (reduces, does not remove, the risk) |
| terminal-escape or bidi tricks in model-written text | control characters and ANSI sequences removed from reasons, errors and excerpts before they are stored or printed |
| credentials leaking through exception text | length cap and redaction of token-like strings before storage |
| oversized input to dodge review | refused, never truncated |
| silently lost audit records | append-only ledger with cross-process locking; `archive()` verifies a copy before dropping the staging file; a failed ledger write blocks the decision |
| leftover model subprocesses | tracked, killed on timeout, deadline, cancellation and interpreter exit |

**Out of scope (it does not defend against).**

* A malicious or buggy caller that skips the board, forges a decision object, or ignores a VETO.
* Anyone who can write to the ledger file. It is append-only by construction of this code, not tamper-evident;
  hash chaining is on the roadmap.
* A compromised or hostile model backend, or a hostile `cmd:` command. The `cmd:` string runs through your shell
  with your full environment unless you pass `env`.
* Adaptive prompt injection that fools every persona, and correlated failure when all personas share one model.
* Secrets inside proposals: the proposal text is sent to whatever model you plug in. The ledger stores only a hash
  unless you opt in, but model-written reasons may quote it.
* Slow or hung models as a resource problem: calls are bounded by timeouts, but an abandoned in-process call
  keeps its thread until the callable returns.

## Examples

* `examples/deploy_gate.py` approve a low-risk configuration change, veto a destructive migration.
* `examples/spend_gate.py` gate purchases, with a hard-veto risk officer and a human sign-off above a limit.

Both run offline with `MockLLM` and say `MOCK MODEL - NOT A REVIEW` when they start.

## Development

```bash
pip install -e ".[dev]"
python -W error -m pytest      # warnings are errors
ruff check . && ruff format --check .
```

CI runs the linter, the format check and the tests on Python 3.11 and 3.12, on Linux and Windows. See
[CHANGELOG.md](CHANGELOG.md) and [ROADMAP.md](ROADMAP.md).

## Weekly council report

`templates/weekly_council_report.md` is a fill-in template for a once-a-week review of the board's decisions: the tally,
the vetoes worth reading (quoted dissent), approvals that turned out badly, per-persona health and ledger integrity.
Every figure is meant to come from the JSONL ledger; the template carries placeholders only, no sample numbers.

## License

MIT. Copyright (c) 2026 ADZ.
