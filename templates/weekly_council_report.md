# Weekly council report - {{week_ending}}

A once-a-week review of every proposal the board judged. Fill each `{{placeholder}}` from the JSONL ledger
(`mirofish-lite board --ledger <file>` lists the records; `mirofish-lite explain <record>` renders one).
Numbers come only from the ledger. If a figure cannot be traced to a ledger line, leave the placeholder and say why.

## 1. The week in one line

{{one_line}}  <!-- e.g. "N proposals, A approved, V vetoed, Q without quorum; the board blocked X" -->

## 2. Tally

| Outcome     | Count            | Share            |
|-------------|------------------|------------------|
| APPROVE     | {{approve_n}}    | {{approve_pct}}  |
| VETO        | {{veto_n}}       | {{veto_pct}}     |
| NO_QUORUM   | {{no_quorum_n}}  | {{no_quorum_pct}}|
| UNRECORDED  | {{unrecorded_n}} | {{unrecorded_pct}}|

Quorum rule in force: {{quorum_rule}} (for example "4 of 5 YES, contrarian advisory").
Model: {{model}}. Mock or synthetic votes this week: {{substituted_n}} (a mock vote is never a review).

## 3. Vetoes worth reading

One block per VETO, newest first. Quote the dissent, do not paraphrase it.

- **{{proposal_title}}** (`{{proposal_hash}}`, {{decided_at}})
  - Voted NO: {{no_personas}}
  - Strongest dissent: "{{dissent_quote}}"
  - What happened next: {{follow_up}}  <!-- dropped / reshaped and re-run / overruled by a human (who, why) -->

## 4. Approvals that went wrong

Approved proposals whose real outcome was bad. This is the section that tunes the board.

- {{proposal_title}}: approved {{decided_at}}, outcome {{real_outcome}}. Which persona saw it coming? {{persona_or_none}}

## 5. Persona health

| Persona        | Votes | NO rate | Unparsable / timed out | Notes        |
|----------------|-------|---------|------------------------|--------------|
| {{persona}}    | {{n}} | {{no_rate}} | {{bad_n}}          | {{notes}}    |

A persona that never says NO, or always does, is not reviewing anything. Flag it here.

## 6. Ledger integrity

- Records this week: {{records_n}}; corrupt lines reported by the reader: {{corrupt_n}}
- Hash chain / proposal hashes checked: {{integrity_check}}

## 7. Changes for next week

- Persona prompt edits: {{prompt_changes}}
- Quorum rule change (needs the owner's sign-off): {{rule_change}}
- Proposals carried over: {{carried_over}}
