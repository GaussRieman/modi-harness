# Evaluation and Quality

## Purpose

Define how Modi Harness behavior is checked beyond unit tests.

## Evaluation Scope

V0.1 evaluates harness behavior, not model intelligence.

Core checks:

- agent and skill loading
- context assembly
- tool-call routing
- policy allow/deny/approval decisions
- interrupt and resume
- output validation
- workspace and trace records

## Fixtures

Add deterministic fixtures:

- sample agent
- sample skill
- sample read-only tool
- sample approval-required tool
- sample task inputs
- expected trace events

## Smoke Scenario

```text
run sample task
-> model requests tool
-> policy requires approval
-> run interrupts
-> approval resumes run
-> output validates
-> workspace and trace can be inspected
```

## Rules

- Prefer deterministic fake models for harness tests.
- Use real LangChain/LangGraph wiring in at least one smoke test.
- Evaluation records should be comparable across model providers.
- Trace replay should explain why a run stopped, resumed, failed, or completed.

## Tests

- golden trace comparison
- fake model deterministic run
- LangGraph smoke run
- approval and denial replay
- output validation issue codes
