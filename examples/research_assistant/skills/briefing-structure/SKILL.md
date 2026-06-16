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

## Stage boundary

This skill runs only in the SUBMIT stage.

- Enter this stage once all user-provided URLs have been fetched successfully and every core comparison dimension has at least one evidence item.
- Do not call `fetch_url`, `source_extract`, workspace/list tools, `recall_memory`, `save_draft`, or `save_artifact` in this stage unless the user explicitly asked for that extra action.
- Only call `submit_output` to deliver the final structured answer.
- Use only extracted evidence from the evidence draft. Do not re-read, quote, or synthesize raw source text.
- Do not write a report body, narrative briefing, or markdown answer in assistant text.

## Procedure

1. Read the evidence draft produced by `source-evaluation`; do not re-fetch or re-compress sources here.
2. Restate the user's research question in `question`.
3. Produce up to 5 entries in `key_findings`. Each entry is one sentence plus a `citation_key` that points into `evidence`.
4. Build `evidence` directly from the evidence draft. Do not re-grade here.
5. Produce up to 3 `open_questions`. Be honest. Empty list is allowed only when the question is genuinely settled.
6. Set `confidence`:
   - `low` if any finding rests only on commentary sources.
   - `medium` if findings rely on secondary or stale primary sources.
   - `high` only if findings are backed by recent primary sources with no unresolved conflicts.
7. Set `risk_label`:
   - `low` if `confidence` is `high` and there are no `open_questions` that block action.
   - `medium` if there are open questions but the core findings are well-cited.
   - `high` if `confidence` is `low` or there are unresolved conflicts.

## Memory Use

- Prefer harness memory already present in context.
- Do not call `recall_memory` when harness memory is present and sufficient.
- Call `recall_memory` at most once only when the task needs missing historical context or the user asks for prior preferences/history.

## Persistence

- **Deliver the answer**: call `submit_output` with the briefing fields as the tool's arguments. The harness validates the call against the contract schema, ends the run on success, and automatically writes the validated payload to `drafts/output.json`.
- Do not call `save_draft` or `save_artifact` unless the user explicitly asks for intermediate files or a publishable Markdown artifact.

## Rules

- Never put speculation into `key_findings` — speculation goes in `open_questions`.
- A `citation_key` referenced in `key_findings` MUST exist in `evidence`. The harness's output contract rejects mismatches.
- The `risk_label` field is required. Do not omit it even when `confidence: high`.
- If the evidence draft covers the core dimensions, submit the answer immediately instead of starting another analysis or fetch pass.
- Final output must be a compact structured summary: short findings, evidence bindings, and unresolved questions only.
- Final output is not a report body; keep it to concise structured fields submitted through `submit_output`.
- `key_findings` must contain no more than 5 entries.
- `evidence` must contain no more than 6 entries.
- `open_questions` must contain no more than 3 entries.
- Each key finding must be no longer than 80 Chinese characters.
- Each evidence entry must be no longer than 120 Chinese characters.
