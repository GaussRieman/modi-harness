# Output Controller

Output Controller validates draft and final outputs.

## Result

```python
class OutputValidationResult(TypedDict):
    status: Literal["draft", "validated", "needs_review", "final", "rejected"]
    output: dict | None
    issues: list[dict]
    required_action: dict | None
```

## Checks

- Schema and required fields.
- Citation and source requirements.
- Risk labels.
- Forbidden content.
- Draft/final boundary.
- Human review requirement.
- Prompt-injection warning.
- Security authorization boundary.

## Rules

- Review-required output stays draft.
- Final output must not claim denied, blocked, or unexecuted side effects.
- Untrusted tool results cannot be presented as instructions.
- Repair loops are owned by Runtime Adapter.
- Accepted drafts and finals are persisted through Workspace Manager.
