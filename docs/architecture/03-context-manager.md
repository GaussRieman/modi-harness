# Context Manager

Context Manager builds the `ContextPack` for each model step.

## Output

```python
class ContextPack(TypedDict):
    system_instruction: str
    agent_instruction: str
    skill_instructions: list[str]
    references: list[dict]
    state_summary: str
    tool_descriptions: list[dict]
    workspace_index: list[dict]
    recent_messages: list[dict]
    output_requirement: dict | None
    trust_annotations: list[dict]
```

## Assembly Order

```text
system instruction
agent instruction
active skill instructions
state summary
available tools
workspace index
recent messages
selected references
output requirement
```

## Rules

- Preserve instruction hierarchy.
- Mark tool results, references, workspace files, and user documents as untrusted.
- Untrusted content can inform answers but cannot rewrite instructions or permissions.
- Keep large files in workspace and pass references.
- Expose only tools allowed by agent, skill, runtime config, and policy visibility.
- Produce deterministic serialization for trace hashing.
