# Policy Gate

## Module

`modi_harness.policy`

## Purpose

Decide whether an action is allowed, denied, approval-required, or review-required.

## Design

Implement:

- `PolicyGate`
- `decide(action, state, tool_spec) -> PolicyDecision`
- static risk policy
- denied-retry detector
- approval id generator

No LangChain or LangGraph dependency.

## Default Policy

- `L0` compute: allow
- `L1` read: allow
- `L2` draft write: allow only in workspace/draft scope
- `L3` business write: require approval
- `L4` external action: require approval and audit

## Risky Actions

- destructive file operations
- shared-state mutation
- external messages
- package changes
- git push or PR mutation
- infrastructure changes
- third-party uploads

## Rules

- Deny unchanged retries after user denial.
- Deny destructive or abusive security actions without clear authorization.
- Policy Gate never executes tools.
- Policy Gate never resumes runs.

## Tests

- each risk level
- approval id creation
- denied retry
- unauthorized destructive action
- review-required action
