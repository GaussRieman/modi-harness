# Output Controller

## Module

`modi_harness.output`

## Purpose

Validate draft and final outputs.

Contract: see [`../architecture/10-output-controller.md`](../architecture/10-output-controller.md).
Types: see [`../types-reference.md`](../types-reference.md).

## Design

Implement:

- `OutputController`
- `validate(draft_output, output_contract, state) -> OutputValidationResult`
- free-form pass-through path
- structured path with JSON Schema validation
- required-field validator
- citation validator
- risk-label validator
- forbidden-content matcher (regex + literal)
- denied-side-effect reconciler (compares output claims to `AgentState.tool_calls` and `denied_actions`)
- prompt-injection warning detector
- security-authorization-boundary check
- review-required flag

No LangChain or LangGraph dependency.

## Rules (impl-specific)

- Free-form contract (`free_form=True`) skips structural checks but still runs forbidden-content, denied-side-effect, prompt-injection, and security checks.
- Review-required output keeps status `needs_review`.
- Final output cannot claim denied, blocked, or unexecuted side effects.
- All issues carry stable `code`, `severity`, optional `field`, `message`, optional `hint`.
- Validation issues are machine-readable for repair, review, and evaluation.

## Issue Codes

See [`../architecture/10-output-controller.md`](../architecture/10-output-controller.md) for the stable code list.

## Tests

- free-form pass-through
- valid structured final
- missing required field
- needs review
- rejected forbidden content
- denied side effect misreported as complete
- prompt-injection warning surfaced
- machine-readable issue codes stable
- issue payload includes hint when applicable
