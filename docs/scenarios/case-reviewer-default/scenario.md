# Scenario: case-reviewer-default

Exercises the case-reviewer agent on a single case file. Demonstrates structured-contract validation and `review_required` write paths.

## Configuration

- **Agent**: `case-reviewer`
- **Permission mode**: agent default (`ask`)
- **Thread**: not used
- **Hooks**: optional `on_approval_request` hook may file an internal audit ticket; not required.
- **Rule packs**: `core`

## What This Proves

- `output_contract` with `risk_label_required=true` and required fields is enforced.
- `write_draft` is L2, **but** the agent's `permission_profile.review_required` lists it, so it routes to `require_review` regardless of mode.
- `OutputController` returns `needs_review`; output is preserved as draft, not promoted to final.
- `project` memory pinned to `case_id` flows into the prompt across runs.

## Inputs

- [`task.json`](./task.json)
- [`tools.md`](./tools.md)
- [`expected.md`](./expected.md)

## Run

```python
response = harness.run_task(
    agent="case-reviewer",
    input={"case_id": "case_2026_0451"},
)
assert response["status"] == "completed"
# Output is in `drafts/`, status `needs_review`.
```
