# Policy Gate

## Module

`modi_harness.policy`

## Purpose

Decide whether an action is allowed, denied, approval-required, or review-required.

Contract: see [`../architecture/06-policy-gate.md`](../architecture/06-policy-gate.md).
Types: see [`../types-reference.md`](../types-reference.md).
Modes: see [`../architecture/14-permission-mode.md`](../architecture/14-permission-mode.md).

## Design

Implement:

- `PolicyGate`
- `decide(ctx: PolicyContext) -> PolicyDecision`
- `visible_tools(agent, mode, state) -> list[str]`
- static risk policy
- denied-retry detector
- approval id generator (ULID)
- rule pack registry: `core` (always on), `coding`, `messaging`, `finance` (opt-in)
- mode-rewrite for `plan`

No LangChain or LangGraph dependency.

## Rule Packs

Each rule pack is a Python module exposing:

```python
def matchers() -> list[ActionMatcher]: ...
```

See `ActionMatcher` in [`../types-reference.md`](../types-reference.md).

Matchers run after the base risk-level decision and can elevate (`allow` → `require_approval` / `require_review` / `deny`) but never lower.

## Rules (impl-specific)

- `decide` is pure with respect to `PolicyContext`.
- Denied-retry uses `RequestedAction.fingerprint` against `state.denied_actions`.
- `plan` mode rewrite happens here, not in Runtime Adapter.
- Mode is taken from `ctx.permission_mode`; selection is resolved by API before `decide` is called.
- Policy Gate never executes tools, never resumes runs, never writes state.

## Settings

```text
MODI_POLICY_RULE_PACKS=core
```

Comma-separated list. `core` is implicitly included even if omitted.

## Tests

- each risk level under each mode for `tool_call`
- `memory_write` decisions per scope
- `output_finalize` decisions per validation status
- preauthorized list in `auto` mode
- denied-retry rejection
- destructive-without-authorization always denied
- mode-rewrite under `plan`
- visible_tools intersection
- rule pack matcher elevation
- pure function: same input → same output
