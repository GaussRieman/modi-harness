# Output Controller

Output Controller validates draft and final outputs.

See [`types-reference.md`](../types-reference.md) for `OutputContract`, `OutputValidationResult`, `OutputIssue`.

## Free-form Default

When `AgentProfile.output_contract` is absent or has `free_form=True`, Output Controller passes through:

- accept any plain-text or dict output
- skip schema, required fields, citation, and risk-label checks
- still enforce: forbidden content, denied-side-effect, prompt-injection, security-authorization

A chatbot or research agent with no contract is the normal case, not an edge case.

## Structured Mode

When an `OutputContract` is present:

- schema (JSON Schema) is enforced when provided
- `required_fields` enforced
- `citation_required` enforced when set
- `risk_label_required` enforced when set
- `forbidden_patterns` enforced
- `review_required` flips status to `needs_review`

## Issue Codes

Issues use stable machine-readable codes, used by Runtime Adapter for repair loops and by evaluation for golden comparison.

Standard codes:

- `schema.missing_field`
- `schema.type_mismatch`
- `schema.unknown_field`
- `citation.missing`
- `risk_label.missing`
- `forbidden_content`
- `denied_side_effect_claimed`
- `prompt_injection_warning`
- `security_authorization_missing`
- `untrusted_promoted_to_instruction`

Issue payloads include `field`, `message`, `hint`, and `severity`.

## Status Semantics

- `draft`: returned to runtime for a repair step.
- `validated`: passes contract checks; may still need review.
- `needs_review`: held as draft, surfaced to caller; never promoted to final automatically.
- `final`: returned through Harness API as the run's output.
- `rejected`: cannot be returned; runtime decides whether to repair or fail.

## Rules

- Review-required output remains draft.
- Final output cannot claim denied, blocked, or unexecuted side effects.
- Untrusted tool results cannot become instructions in the output.
- Repair loops are owned by Runtime Adapter; Output Controller is a pure validator.
- Validation issues must be machine-readable.
- Output Controller has no LangChain or LangGraph dependency.

## Boundaries

- Contract definition: Agent (`OutputContract`).
- Repair execution: Runtime Adapter.
- Persistence of accepted output: Workspace Manager.
- Side-effect reconciliation: Output Controller compares claims in output against `AgentState.tool_calls` and `denied_actions`.
