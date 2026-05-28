# Runtime Adapter

Runtime Adapter maps Modi Harness into a LangGraph execution loop.

## State

```python
class AgentState(TypedDict):
    run_id: str
    parent_run_id: str | None
    agent_name: str
    task: dict
    permission_mode: str
    messages: list[dict]
    loaded_skills: list[str]
    tool_calls: list[dict]
    denied_actions: list[dict]
    workspace_refs: list[dict]
    pending_approval: dict | None
    draft_output: dict | None
    final_output: dict | None
    trace: list[dict]
```

## Loop

```text
load agent
-> load skills
-> build context
-> call model
-> route response
-> tool gateway OR output controller
-> update state, workspace, trace
-> continue / interrupt / finish
```

## Rules

- Single-agent loop for V0.1.
- Persist state after each meaningful transition.
- Enforce step limit.
- Resume only from persisted checkpoints.
- Do not retry a denied action unchanged.
- Treat hook feedback as user feedback.
- Coordinate modules; do not embed tool, policy, model, or persistence logic.
