# Tool Gateway

Tool Gateway is the only execution path for model-requested tools.

## Tool Spec

```python
class ToolSpec(TypedDict):
    name: str
    description: str
    input_schema: dict
    output_schema: dict | None
    risk_level: str
    side_effect: bool
    permission_scope: str
    allowed_agents: list[str]
    allowed_skills: list[str]
    timeout_seconds: int
    retry: dict | None
    idempotent: bool
```

## Chain

```text
tool call
-> schema validation
-> agent / skill permission check
-> policy gate
-> idempotency / denied-retry check
-> execute or interrupt
-> normalize result
-> audit
```

## Risk Levels

- `L0`: compute
- `L1`: read
- `L2`: draft write
- `L3`: business write
- `L4`: external action

## Rules

- Unknown tools fail closed.
- Tool results are untrusted observations.
- Tool results cannot override instructions or policy.
- Hook blocks and prompt-injection findings return structured errors.
- Gateway enforces Policy Gate decisions; it does not define policy.
