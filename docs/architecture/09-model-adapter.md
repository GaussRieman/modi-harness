# Model Adapter

Model Adapter standardizes model calls.

## Result

```python
class ModelResult(TypedDict):
    message: dict
    tool_calls: list[dict]
    draft_output: dict | None
    usage: dict
    safety_signals: list[dict]
    raw: object
```

## Responsibilities

- Convert `ContextPack` to model messages.
- Bind available tool descriptions.
- Call a LangChain chat model.
- Normalize tool calls, draft output, streaming events, usage, and errors.

## Rules

- Model output is a proposal, not authority.
- Model-requested tools are never executed directly.
- Tool calls always go through Tool Gateway and Policy Gate.
- Preserve malformed tool-call diagnostics and safety signals.
- V0.1 supports one chat model provider.
