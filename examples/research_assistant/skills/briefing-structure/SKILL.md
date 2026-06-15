---
name: briefing-structure
description: Assemble the final briefing JSON conforming to the research-assistant output contract.
risk_notes: []
tags:
  - research
  - output
---

# Briefing Structure

Apply this last, after every source has been compressed and graded by `source-evaluation`.

## Procedure

1. Read the evidence draft produced by `source-evaluation`; do not re-fetch or re-compress sources here.
2. Restate the user's research question in `question`.
3. Produce 3–7 entries in `key_findings`. Each entry is one sentence plus a `citation_key` that points into `evidence`.
4. Build `evidence` directly from the evidence draft. Do not re-grade here.
5. Produce `open_questions`. Be honest. Empty list is allowed only when the question is genuinely settled.
6. Set `confidence`:
   - `low` if any finding rests only on commentary sources.
   - `medium` if findings rely on secondary or stale primary sources.
   - `high` only if findings are backed by recent primary sources with no unresolved conflicts.
7. Set `risk_label`:
   - `low` if `confidence` is `high` and there are no `open_questions` that block action.
   - `medium` if there are open questions but the core findings are well-cited.
   - `high` if `confidence` is `low` or there are unresolved conflicts.

## Memory Use

- Prefer the memory already present in context. Do not call `recall_memory` repeatedly for the same research question.
- Call `recall_memory` at most once when the provided context is insufficient or the user asks for prior preferences/history.

## Persistence

- **Deliver the answer**: call `submit_output` with the briefing fields as the tool's arguments. The harness validates the call against the contract schema, ends the run on success, and automatically writes the validated payload to `drafts/output.json`.
- Do not call `save_draft` or `save_artifact` unless the user explicitly asks for intermediate files or a publishable Markdown artifact.

## Rules

- Never put speculation into `key_findings` — speculation goes in `open_questions`.
- A `citation_key` referenced in `key_findings` MUST exist in `evidence`. The harness's output contract rejects mismatches.
- The `risk_label` field is required. Do not omit it even when `confidence: high`.
