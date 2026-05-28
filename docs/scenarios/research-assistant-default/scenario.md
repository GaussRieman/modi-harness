# Scenario: research-assistant-default

Exercises the research-assistant agent on a single research question, demonstrating both `plan` and `ask` modes.

## Configuration

- **Agent**: `research-assistant`
- **Permission mode**: per run override — first `plan`, then `ask`
- **Thread**: not used (one-shot run)
- **Hooks**: none required
- **Rule packs**: `core`

## What This Proves

- `plan` mode never fetches real URLs: tools either dry-run or return `would_do`.
- Fetched pages enter the prompt as `<untrusted source_kind="tool_result">` blocks.
- Citation enforcement: `OutputController` rejects `key_findings` without a citation key.
- Structured `output_contract` validates against required fields.
- `write_draft` is L2 with `dry_run_supported=True`, so drafts go to the run workspace under `drafts/`.

## Inputs

- [`task.json`](./task.json)
- [`tools.md`](./tools.md)
- [`expected.md`](./expected.md)

## Run

```python
# plan-mode dry run
plan_response = harness.run_task(
    agent="research-assistant",
    input=task,
    permission_mode="plan",
)

# ask-mode real run
real_response = harness.run_task(
    agent="research-assistant",
    input=task,
    permission_mode="ask",
)
```
