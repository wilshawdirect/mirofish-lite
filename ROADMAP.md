# Roadmap

Nothing below is built yet. Items are ordered by how much they would improve the trustworthiness of a board's
output, not by date.

## Next

1. **Replay harness for tuning thresholds.** Feed a file of past proposals with known good/bad outcomes (and the
   ledger of real decisions) through a board and report approve / veto / no-quorum rates, false approvals and
   false vetoes per `k`, `of` and `on_error` setting. Quorum thresholds are policy; this is how to choose them
   with evidence instead of by feel.
2. **Calibration scoring per persona.** Using the same labelled cases, score each persona's agreement with the
   eventual outcome, how well its `confidence` is calibrated (Brier score, reliability table), and how often it
   is the lone dissenter. Surface personas that add nothing or always agree with another one.
3. **An MCP server.** Expose `decide` and `explain` as tools so an agent can ask for a board opinion through the
   Model Context Protocol, with the ledger path and persona set fixed by server configuration rather than by the
   caller.

## Later

* Hash-chained ledger records so truncation and edits are detectable (tamper-evident, still not tamper-proof).
* Async / streaming model adapters and an `asyncio` entry point.
* Persona prompt versioning and A/B comparison across prompt versions in the replay harness.
* Per-persona model routing (different models for different personas, to reduce correlated failure).
* Optional second-round "deliberation" where personas see the dissent before a final vote (measured against the
  one-round baseline before it is made a default).
* Packaging: PyPI release and typed public API review.

## Explicitly not planned

* Making the board an enforcement mechanism. It stays advisory; enforcement belongs in the caller, with a human
  approval for irreversible or financial actions.
