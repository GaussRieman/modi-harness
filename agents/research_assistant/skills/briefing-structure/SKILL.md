---
name: briefing-structure
description: Assemble the final briefing JSON conforming to the research-assistant output contract.
risk_notes: []
tags:
  - research
  - output
---

# Briefing Structure

Apply this last, after fetched sources have been read and graded by `source-evaluation`.

## Stage boundary

This skill runs only in the SUBMIT stage.

- Enter this stage once all user-provided URLs have been fetched successfully and there is at least one evidence-backed finding to submit.
- Do not call `fetch_url`, `source_extract`, workspace/list tools, `recall_memory`, `save_draft`, or `save_artifact` in this stage unless the user explicitly asked for that extra action.
- Only call `submit_output` to deliver the final structured answer.
- Use only extracted evidence from the evidence draft. Do not re-read, quote, or synthesize raw source text.
- Do not write a report body, narrative briefing, markdown answer, source table, JSON draft, or sufficiency checklist in assistant text.

## Procedure

1. Read the evidence draft and completed task summaries; do not re-fetch or re-compress sources here.
2. Copy the user-confirmed question into `research_question` without broadening it.
3. Write `executive_summary` in 2-4 sentences that directly answer the question.
4. Build one `task_results` entry for every completed task, preserving plan order. Each entry contains `task`, `result`, `evidence`, and `limitations`.
5. Put each fact under the single task where it contributes most. Do not repeat task results in the executive summary or another task.
6. Add only evidence-backed `recommendations`. Use an empty array when the sources do not support a recommendation.
7. Put limitations that affect the whole answer in `source_limitations`; keep task-specific gaps with their task result.

## Memory Use

- Prefer harness memory already present in context.
- Do not call `recall_memory` when harness memory is present and sufficient.
- Call `recall_memory` at most once only when the task needs missing historical context or the user asks for prior preferences/history.

## Persistence

- **Deliver the answer**: call `submit_output` with the briefing fields as the tool's arguments. The harness validates the call against the contract schema, ends the run on success, and automatically writes the validated payload to `drafts/output.json`.
- Do not call `save_draft` or `save_artifact` unless the user explicitly asks for intermediate files or a publishable Markdown artifact.

## Rules

- Never present speculation as a task result. Put uncertainty in that result's `limitations`.
- Every completed task must appear exactly once in `task_results`; do not invent tasks that were not approved and completed.
- Every task result must carry at least one source-bound evidence item unless the result is explicitly an inference from earlier evidenced tasks.
- For an inference, label it as an inference and cite the earlier evidence it uses.
- `research_question`, `executive_summary`, `task_results`, `recommendations`, and `source_limitations` are all required.
- Partial coverage is acceptable: answer the supported portion and state the exact boundary in `limitations`.
- Avoid generic phrases such as "资料不足" or "需要更多信息". Name the missing field, source, time range, or comparison instead.
- The final output should make the plan and its completed work visible without repeating the same facts in several sections.
