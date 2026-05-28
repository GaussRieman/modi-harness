# Scenario: release-coordinator-default

Exercises the release-coordinator agent on a release readiness check. Demonstrates `auto` mode with mixed preauthorized and review-required tools, plus the `coding` rule pack.

## Configuration

- **Agent**: `release-coordinator`
- **Permission mode**: agent default (`auto`)
- **Thread**: not used
- **Hooks**:
  - `pre_tool_use` matcher `{tool: jira_create_release_ticket}` may require an epic id; recommended for production deployments.
- **Rule packs**: `core` + `coding`

## What This Proves

- `auto` mode + `preauthorized: [jira_create_release_ticket]` allows the L3 ticket file without prompting.
- `review_required: [send_slack_release_summary]` interrupts the L4 messaging step regardless of mode.
- `coding` rule pack denies any model-proposed git mutation tool, even though such a tool is not declared on the agent.
- Hook-driven external preconditions (epic id) integrate cleanly without modifying the agent.
- `denied-retry` records the model's repeated attempt at a denied action.

## Inputs

- [`task.json`](./task.json)
- [`tools.md`](./tools.md)
- [`expected.md`](./expected.md)

## Run

```python
response = harness.run_task(
    agent="release-coordinator",
    input={
        "release_tag": "v2.7.0",
        "release_branch": "release/2.7",
        "channel": "#releases",
    },
    options={"rule_packs": ["core", "coding"]},
)
```
