---
name: briefing-structure
description: Assemble the final briefing JSON conforming to the research-assistant output contract.
risk_notes: []
tags:
  - research
  - output
---

# Briefing Structure

Apply this last, after every source has been graded by `source-evaluation`.

## Procedure

1. Restate the user's research question in `question`.
2. Produce 3–7 entries in `key_findings`. Each entry is one sentence plus a `citation_key` that points into `evidence`.
3. Build `evidence` directly from the `source-evaluation` output. Do not re-grade here.
4. Produce `open_questions`. Be honest. Empty list is allowed only when the question is genuinely settled.
5. Set `confidence`:
   - `low` if any finding rests only on commentary sources.
   - `medium` if findings rely on secondary or stale primary sources.
   - `high` only if findings are backed by recent primary sources with no unresolved conflicts.
6. Set `risk_label`:
   - `low` if `confidence` is `high` and there are no `open_questions` that block action.
   - `medium` if there are open questions but the core findings are well-cited.
   - `high` if `confidence` is `low` or there are unresolved conflicts.

## Persistence

- Render a one-page Markdown summary and call `save_artifact` with `name="briefing.md"` — this is the human-readable version.
- **Deliver the answer**: call `submit_output` with the briefing fields as the tool's arguments. The harness validates the call against the contract schema, ends the run on success, and automatically writes the validated payload to `drafts/output.json`.

## Rules

- Never put speculation into `key_findings` — speculation goes in `open_questions`.
- A `citation_key` referenced in `key_findings` MUST exist in `evidence`. The harness's output contract rejects mismatches.
- The `risk_label` field is required. Do not omit it even when `confidence: high`.
