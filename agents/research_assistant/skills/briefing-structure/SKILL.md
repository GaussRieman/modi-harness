---
name: briefing-structure
description: Draft and verify a source-bound research briefing across autonomous Nodes.
risk_notes: []
tags:
  - research
  - output
---

# Briefing Structure

This skill guides `synthesize_briefing` and `verify_briefing`. The synthesis
Node converts committed evidence into a draft. The verification Node checks,
repairs, and returns the terminal briefing.

## Node Boundaries

- `synthesize_briefing` may use only its research-plan and EvidenceBundle
  inputs plus `generate_research_digest`.
- `verify_briefing` receives the plan, EvidenceBundle, and committed draft. It
  uses `judge_research_digest` when needed and repairs inside the same Node.
- Propose `complete_node` only with the active Node's declared output shape.
- Do not broaden the question or introduce facts absent from EvidenceBundle.

## Required Fields

The terminal `verify_briefing` output must contain:

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
- Build task results that preserve the committed research plan.
- Each task result contains `task`, `result`, `evidence`, and `limitations`.
- A supported task result has one or more HTTP(S) evidence URLs. If public
  evidence is unavailable, use an empty `evidence` array and a non-empty,
  specific task `limitations` array; do not invent a citation.
- Add only evidence-backed `recommendations`; use an empty array when sources do
  not support a recommendation.
- Put whole-answer limitations in `source_limitations`. It must be non-empty
  whenever any task result has no evidence.
- Never present speculation as a task result.
- Avoid generic phrases such as "资料不足" or "需要更多信息"; name the missing
  field, source, time range, or comparison instead.
