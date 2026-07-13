---
name: briefing-structure
description: Shape the generated research digest final_output for the output contract.
risk_notes: []
tags:
  - research
  - output
---

# Briefing Structure

This skill describes the `final_output` field produced by
`generate_research_digest`. Brain does not write this payload; Brain only
consumes the recorded digest artifact and proposes `complete_node`.

## Output Boundary

- `complete_node` may only use `digest.final_output` or explicit human input.
- Do not re-read or re-synthesize raw source content during finalization.
- Do not call `fetch_url`, `source_extract`, workspace/list tools,
  `recall_memory`, `save_draft`, or `save_artifact` from finalization unless
  the user explicitly asks for that extra action.
- Do not write a report body, narrative briefing, markdown answer, source
  table, JSON draft, or sufficiency checklist in assistant text.

## Required Fields

`final_output` must match the research-assistant output contract:

- `research_question`
- `executive_summary`
- `task_results`
- `recommendations`
- `source_limitations`

## Rules

- Copy the user-confirmed question without broadening it.
- `executive_summary` should directly answer the supported part of the question.
- `executive_summary` must be a compact synthesis from the digest artifact; it
  must not concatenate source titles, coverage fields, or raw evidence snippets
  into a long paragraph.
- Build one `task_results` entry per planned task, preserving plan order.
- Each task result contains `task`, `result`, `evidence`, and `limitations`.
- Add only evidence-backed `recommendations`; use an empty array when sources do
  not support a recommendation.
- Put whole-answer limitations in `source_limitations`.
- Never present speculation as a task result.
- Avoid generic phrases such as "资料不足" or "需要更多信息"; name the missing
  field, source, time range, or comparison instead.
