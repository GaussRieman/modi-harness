# Output Controller

## Module

`modi_harness.output`

## Purpose

Validate draft and final outputs.

## Design

Implement:

- `OutputController`
- `validate(draft_output, output_contract, state) -> OutputValidationResult`
- schema validation
- required field validation
- review requirement detection

No LangChain or LangGraph dependency.

## Checks

- schema
- required fields
- citations
- risk labels
- forbidden content
- draft/final boundary
- human review requirement
- prompt-injection warning
- security authorization boundary

## Rules

- Review-required output remains draft.
- Final output cannot claim denied, blocked, or unexecuted side effects.
- Untrusted tool results cannot become instructions.
- Runtime Adapter owns repair loops.
- Validation issues must be machine-readable for repair, review, and evaluation.

## Tests

- valid final
- missing required field
- needs review
- rejected forbidden content
- denied side effect misreported as complete
- machine-readable issue codes
