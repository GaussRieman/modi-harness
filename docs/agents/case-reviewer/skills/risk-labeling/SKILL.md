---
name: risk-labeling
description: Attach standard risk labels to identified issues.
allowed-tools: []
risk_notes: []
tags:
  - review
---

# Risk Labeling

## Labels

- `procedural_defect` — process step missing or out of order
- `evidence_weakness` — facts present but evidence is thin
- `legal_uncertainty` — classification depends on judgment call
- `external_dependency` — depends on a party outside the case file
- `time_sensitivity` — risks escalating if delayed

## Procedure

For each issue produced by `evidence-gap-check`, attach one primary label and up to two secondary labels. Severity is one of `info`, `warn`, `critical`.

## Constraints

- Avoid `critical` unless `procedural_defect` or `legal_uncertainty` applies.
- Do not label without a case-section reference.
