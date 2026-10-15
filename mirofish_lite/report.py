"""Human-readable rendering of a decision dict (as produced by ``Decision.to_dict()``).

Every string that comes from the record is passed through ``clean_text`` first,
so a crafted decision file (or a model-written reason) cannot inject terminal
escape sequences into the output.
"""

from __future__ import annotations

from typing import Any, Mapping

from .textutil import clean_text

REQUIRED_KEYS = ("outcome", "votes", "tally", "proposal_hash")

_EXIT = {"APPROVE": 0, "VETO": 1, "NO_QUORUM": 2, "UNRECORDED": 3}
MOCK_BANNER = "MOCK MODEL - NOT A REVIEW"


def explain_dict(d: Mapping[str, Any]) -> str:
    missing = [k for k in REQUIRED_KEYS if k not in d]
    if missing:
        raise ValueError(f"not a decision record: missing {', '.join(missing)}")
    c = clean_text
    outcome = c(d["outcome"])
    tally = d.get("tally") or {}
    rule = d.get("quorum_rule") or {}
    config = d.get("config") or {}
    model = c(config.get("llm", "unknown"))
    votes = d.get("votes") or []
    substituted = d.get("substituted")
    if substituted is None:
        substituted = sum(1 for v in votes if v.get("status", "ok") != "ok")
    lines = []
    if model == "mock":
        lines.append(f"*** {MOCK_BANNER}: scripted votes, nothing was actually reviewed ***")
    lines += [
        f"Outcome:   {outcome} (exit code {_EXIT.get(outcome, '?')})",
        f"Proposal:  sha256 {c(d['proposal_hash'])[:16]}...  at {c(d.get('ts', '?'))}",
        f"Model:     {model}",
        f"Rule:      {c(rule.get('describe', rule))}",
        (
            f"Tally:     YES {c(tally.get('yes', 0))}  NO {c(tally.get('no', 0))}  ABSTAIN {c(tally.get('abstain', 0))}"
            f"  (eligible weight {c(tally.get('eligible_weight', 0))}, need {c(tally.get('k', '?'))})"
        ),
        f"Substituted votes (error / timeout / deadline / unparsable): {c(substituted)}",
    ]
    adv = tally.get("advisory") or {}
    if any(adv.values()):
        lines.append(
            f"Advisory:  YES {c(adv.get('yes', 0))}  NO {c(adv.get('no', 0))}  ABSTAIN {c(adv.get('abstain', 0))}  (not counted)"
        )
    if d.get("rationale"):
        lines.append(f"Why:       {c(d['rationale'])}")
    for note in d.get("notes") or []:
        lines.append(f"Note:      {c(note)}")

    if votes:
        lines.append("")
        lines.append("Votes:")
        width = max(len(c(v.get("persona", ""))) for v in votes)
        for v in votes:
            flags = []
            if not v.get("in_quorum", True):
                flags.append("advisory")
            if v.get("hard_veto"):
                flags.append("hard-veto")
            if v.get("weight", 1) != 1:
                flags.append(f"weight {c(v.get('weight'))}")
            if v.get("status", "ok") != "ok":
                flags.append(f"substituted: {c(v.get('status'))}")
            conf = v.get("confidence")
            conf_txt = f" ({conf:.2f})" if isinstance(conf, (int, float)) and not isinstance(conf, bool) else ""
            flag_txt = f" [{', '.join(flags)}]" if flags else ""
            lines.append(
                f"  {c(v.get('persona', '?')):<{width}}  {c(v.get('vote', '?')):<7}{conf_txt}{flag_txt}  {c(v.get('reason', ''))}"
            )

    dissent = d.get("dissent") or []
    lines.append("")
    if dissent:
        lines.append("Dissent (who said NO and why):")
        for x in dissent:
            tag = " [board-substituted NO]" if x.get("synthetic") else ""
            lines.append(f"  - {c(x.get('persona'))}{tag}: {c(x.get('reason', ''))}")
    else:
        lines.append("Dissent: none")

    for pf in d.get("parse_failures") or []:
        lines.append(f"Parse failure: {c(pf.get('persona'))}: {c(pf.get('error'))}")
    for er in d.get("errors") or []:
        lines.append(f"Error: {c(er.get('persona'))} [{c(er.get('kind'))}]: {c(er.get('message'))}")
    lines.append(f"Calls: {c(d.get('calls', '?'))}   Duration: {c(d.get('duration_s', '?'))}s")
    return "\n".join(lines)
