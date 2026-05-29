# Scenario: release-coordinator-with-research

Exercises subagent delegation: release-coordinator delegates upstream research to research-assistant before drafting release notes. Demonstrates `allowed_subagents`, permission mode tightening, and untrusted child output.

## Configuration

- **Agent**: `release-coordinator`
- **Subagent**: `research-assistant`
- **Permission mode**: agent default (`auto` for parent; child inherits `auto` or tighter)
- **Thread**: not used

## What This Proves

- `allowed_subagents: [research-assistant]` permits the delegation call.
- `delegate_to_research_assistant` is auto-registered as a tool by the harness.
- Child output is wrapped as untrusted (`source_kind: subagent_result`) in the parent context.
- Delegation appears in the trace as a `tool_result` event with `tool_name: delegate_to_research_assistant`.
- Permission mode tightening prevents the child from running in a laxer mode than the parent.
- Parent's denied-actions list propagates to the child.

## Inputs

- [`task.json`](./task.json)
- [`expected.md`](./expected.md)

## Run

```python
response = harness.run_task(
    agent="release-coordinator",
    input={
        "goal": "Prepare release notes for v2.5. Research what breaking changes were introduced in upstream dependency 'libcore' between v3.1 and v3.2, then summarize them in the release notes draft.",
        "messages": [
            {
                "role": "user",
                "content": "Prepare release notes for v2.5. Research what breaking changes were introduced in upstream dependency 'libcore' between v3.1 and v3.2, then summarize them in the release notes draft.",
            }
        ],
    },
)
```
