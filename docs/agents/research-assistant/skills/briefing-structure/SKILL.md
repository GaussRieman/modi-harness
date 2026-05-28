---
name: briefing-structure
description: Assemble final briefing JSON conforming to the research-assistant output contract.
allowed-tools:
  - write_draft
risk_notes: []
tags:
  - research
  - output
---

# Briefing Structure

Use last, after sources are graded.

## Procedure

1. Restate `question`.
2. Produce 3–7 `key_findings`. Each is one sentence + one citation key.
3. Produce `evidence` list aligned with `source-evaluation` output.
4. Produce `open_questions`. Be honest. Empty list is allowed only when the question is genuinely settled.
5. Set `confidence`:
   - `unverified` in plan mode
   - `low` if any finding has only commentary sources
   - `medium` if findings have secondary or stale primary sources
   - `high` only if findings have recent primary sources and no unresolved conflicts

## Rules

- Never include speculation in `key_findings`.
- A finding without a citation key is a contract violation.
- Drafts are written to the run workspace via `write_draft`.
