# Evaluation and Quality

## Purpose

Define how Modi Harness behavior is checked beyond unit tests.

## Evaluation Scope

V0.1 evaluates harness behavior, not model intelligence.

Core checks:

- agent and skill loading (multi-source, duplicate-name failure)
- context assembly (deterministic hash, trust annotations, tool intersection)
- memory selection (scope ordering, token budget)
- tool-call routing (denied-retry guard, dry-run, idempotency)
- policy allow/deny/approval/review decisions under each permission mode
- hook dispatch (proceed/block/redirect, matcher, on_failure)
- interrupt and resume
- output validation (free-form pass-through, structured contract, issue codes)
- workspace and trace records
- prompt wrapping for untrusted content

## Fixtures

Deterministic fixtures:

- sample agent (one per domain in [`../agents/`](../agents/))
- sample skill packages (bundled under each agent)
- sample read-only tool
- sample approval-required tool
- sample dry-run-supported tool
- sample memory records per scope
- sample hook configurations
- sample task inputs (in [`../scenarios/`](../scenarios/))
- expected trace events per scenario

## Smoke Scenarios

S1 (governance happy path):
```text
run sample task
-> model requests tool
-> policy requires approval
-> run interrupts
-> approval resumes run
-> output validates
-> workspace and trace can be inspected
```

S2 (denied retry):
```text
user rejects a tool call
-> model proposes same call again
-> tool gateway blocks pre-policy
-> trace records denied_retry
```

S3 (plan mode):
```text
run with permission_mode=plan
-> no L2+ side effects executed
-> dry-run tool returns would_do
-> output reports plan, not result
```

S4 (memory round-trip):
```text
add_memory(feedback)
-> run a related task
-> context includes the feedback block
-> trace records memory_selection
```

S5 (hook block):
```text
configure pre_tool_use hook with on_failure=block
-> tool call blocked
-> recorded as DeniedAction
-> same call retried fails fast
```

S6 (free-form output):
```text
agent without output_contract
-> validator passes free-form text
-> still rejects denied-side-effect claim
```

## Rules

- Prefer deterministic fake models for harness tests.
- Use real LangChain/LangGraph wiring in at least one smoke test.
- Evaluation records are comparable across model providers.
- Trace replay must explain why a run stopped, resumed, failed, or completed.
- Issue codes from Output Controller are stable; evaluation compares against the documented list.

## Tests

- golden trace comparison per smoke scenario
- fake model deterministic run
- LangGraph smoke run
- approval and denial replay
- output validation issue code stability
- multi-domain sample agents all complete without policy errors in default mode
