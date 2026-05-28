# Policy Gate

Policy Gate decides whether an action is allowed, denied, approval-required, or review-required.

## Decision

```python
class PolicyDecision(TypedDict):
    decision: Literal["allow", "deny", "require_approval", "require_review"]
    reason: str
    approval_id: str | None
    review_requirement: dict | None
    denied_retry: bool
    audit: dict
```

## Inputs

- Agent, skill, tool, user, task, state.
- Requested action and target resource.
- Risk level, side effect, permission mode.
- Prior approvals and denials.

## Default Policy

- `L0` compute: allow.
- `L1` read: allow.
- `L2` draft write: allow only in workspace/draft scope.
- `L3` business write: require approval.
- `L4` external action: require approval and audit.

## Rules

- Deny unchanged retries after user denial.
- Deny destructive or abusive security actions without clear authorization.
- Treat shared-state mutation, external messages, package changes, pushes, PR changes, infrastructure changes, and third-party uploads as risky.
- Policy Gate never executes tools or resumes runs.
