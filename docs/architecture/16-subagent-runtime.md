# Subagent Runtime

Future module. Not required for V0.1.

Subagent Runtime lets an agent delegate a bounded task to another agent through an agent tool.

## Flow

```text
agent tool call
-> create child run
-> load child agent and skills
-> build isolated child context
-> execute child loop
-> return child result
-> update parent state
```

## Constraints

- Child context, state, tools, skills, approvals, and workspace scope are isolated.
- Child permissions are checked independently.
- Trace preserves `root_run_id`, `parent_run_id`, and child `run_id`.
- Child artifacts are referenced back to the parent.
- Parent denial history prevents child retries of the same denied action.

## Deferred Because

Subagents add recursion, trace hierarchy, approval propagation, workspace scoping, and error recovery. V0.1 should prove the single-agent loop first.
