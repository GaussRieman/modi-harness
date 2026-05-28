# Scenario: support-bot-default

Exercises the support-bot agent on a billing-dispute conversation that should escalate to a human.

## Configuration

- **Agent**: `support-bot`
- **Permission mode**: agent default (`ask`)
- **Thread**: `thread_demo_001` (conversation memory enabled)
- **Hooks**: optional `session_start` hook may inject brand voice; not required.
- **Rule packs**: `core`

## What This Proves

- Free-form output passes `OutputController` without a structured contract.
- Conversation memory plus user memory flow into the prompt as `memory_blocks`.
- L4 escalation tool triggers `require_review` (per agent's `review_required` list) and interrupts the run.
- Approval flow resumes the run and delivers a free-form reply.
- `denied-retry` guard kicks in if the model attempts the same escalation twice.

## Inputs

- [`task.json`](./task.json) — passed as `RunTaskRequest.input`.
- [`tools.md`](./tools.md) — tools the harness must register before running.
- [`expected.md`](./expected.md) — expected runtime behavior and trace events.

## Run

```python
from modi_harness import ModiHarness
import json

harness = ModiHarness(agents_dir="docs/agents")
with open("docs/scenarios/support-bot-default/task.json") as f:
    task = json.load(f)

harness.start_thread(agent="support-bot", options={"thread_id": "thread_demo_001"})
response = harness.run_task(
    agent="support-bot",
    input=task,
    thread_id="thread_demo_001",
)
```
