# Policy Gate

> **Note.** This doc describes the gate's implementation contract. For the conceptual model (permission layers, modes, interaction, execution), see [`permissions.md`](./permissions.md).

Policy Gate decides whether an action is allowed, denied, approval-required, or review-required.

See [`types-reference.md`](../types-reference.md) for `PolicyContext`, `RequestedAction`, `PolicyDecision`.

## Input

Policy Gate has a single explicit input: `PolicyContext`. It does not read state, settings, or other modules out of band.

```python
def decide(ctx: PolicyContext) -> PolicyDecision: ...
```

`PolicyContext` carries: agent, optional active skill, optional tool spec, current state, requested action, permission mode.

## Default Policy

Risk matrix is anchored to risk level **and** permission mode. See [`14-permission-mode.md`](./14-permission-mode.md) for the full matrix.

Summary in `ask` mode for `RequestedAction.kind = "tool_call"`:

- `L0` compute: allow.
- `L1` read: allow.
- `L2` draft write: allow only when target is inside the run workspace or draft scope.
- `L3` business write: require approval.
- `L4` external action: require approval and audit.

`RequestedAction.kind = "memory_write"`:

- `conversation`, `agent` scope: allow.
- `user`, `project` scope: require approval.
- write whose source trace includes an untrusted tool result without user round-trip: deny.

`RequestedAction.kind = "output_finalize"`:

- allow when `OutputController.status` is `validated` or `final`.
- require_review when status is `needs_review`.
- deny when status is `rejected`.

## Risk Lists Are Pluggable

The list of "risky actions" is **not hard-coded** in Policy Gate code. Coding-domain examples (git push, package install, infrastructure changes) live in an optional rule pack and are loaded only when the deployment enables it.

Default rule packs shipped:

- `core`: denied-retry, destructive-without-authorization, security abuse. Always on.
- `coding`: git mutations, package management, push/PR, infra config. Opt-in.
- `messaging`: external messaging, third-party uploads. Opt-in.
- `finance`: monetary writes, payment APIs. Opt-in.

A rule pack contributes additional risky-action matchers and elevated decisions; it cannot override `core`.

## Decision Properties

- `denied_retry=True` when the action's `fingerprint` matches a `DeniedAction` already recorded in state.
- `approval_id` is populated only for `require_approval`.
- `review_requirement` is populated only for `require_review`.
- `audit` carries machine-readable rule matches; useful for trace and compliance review.

## Rules

- A deny applies regardless of permission mode for: denied-retry, destructive-without-authorization, security abuse.
- Policy Gate never executes tools, never resumes runs, never edits state, never mutates workspace.
- Policy Gate never depends on LangChain or LangGraph.
- Policy Gate is pure with respect to its input: same `PolicyContext` → same `PolicyDecision`.
- Mode `plan` rewrites every L2+ allow into `require_review`; this rewrite happens here, not in Runtime Adapter.

## Boundaries

- Tool execution: Tool Gateway.
- Approval acceptance: Harness API (`approve_action` / `reject_action`).
- Persistence of denials: Workspace Manager via state snapshot.
- Risk level of a tool: tool author via `ToolSpec.risk_level`.
