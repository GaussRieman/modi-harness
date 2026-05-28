---
name: evidence-gap-check
description: Map case facts to available evidence and surface unsupported facts.
allowed-tools:
  - read_case
  - read_evidence
risk_notes:
  - Do not treat statements as verified facts.
tags:
  - review
---

# Evidence Gap Check

## Procedure

1. Extract every claim from the case under section labels.
2. For each claim, list the evidence items that support it.
3. Mark unsupported claims as `evidence_gap` with reason `no_supporting_item`.
4. Mark weakly supported claims with reason `single_uncorroborated_source` or `dated_source`.
5. Mark conflicting evidence with reason `evidence_conflict` and list the conflicting items.

## Constraints

- Do not invent evidence.
- Do not treat narrative statements as verified facts.
- Do not produce final decisions.
