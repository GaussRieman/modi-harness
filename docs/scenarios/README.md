# Modi Harness — Scenarios

A **scenario** is an end-to-end test fixture: a task input, the tools the harness must have registered, and the expected behavior. Scenarios reference agents from [`../agents/`](../agents/); they do not redefine them.

```text
scenarios/<scenario-name>/
├── scenario.md        # which agent, mode, options; what this scenario proves
├── task.json          # the run input passed to RunTaskRequest.input
├── tools.md           # tools that must be registered for this scenario to run
└── expected.md        # expected runtime behavior, trace events, validation result
```

`task.json` is the value of `RunTaskRequest.input` (not the full request). The full request is constructed by the caller as:

```python
RunTaskRequest(
    agent="<agent-name>",          # from scenario.md
    input=<task.json contents>,
    options={...},                 # from scenario.md
    permission_mode=<from scenario.md or None>,
    thread_id=<optional>,
    parent_run_id=None,
)
```

## Available Scenarios

| Scenario | Agent | Mode | Proves |
|---|---|---|---|
| [support-bot-default](./support-bot-default/) | support-bot | ask | conversation memory, free-form output, L4 escalation flow |
| [research-assistant-default](./research-assistant-default/) | research-assistant | plan, then ask | plan mode dry-run, untrusted source wrapping, citation enforcement |
| [case-reviewer-default](./case-reviewer-default/) | case-reviewer | ask | structured contract, review-required write_draft, evidence-gap output |
| [release-coordinator-default](./release-coordinator-default/) | release-coordinator | auto | preauthorized vs review-required tools, hook integration, coding rule pack |

## Running

```python
from modi_harness import ModiHarness
import json

harness = ModiHarness(agents_dir="docs/agents")
with open("docs/scenarios/support-bot-default/task.json") as f:
    task = json.load(f)

response = harness.run_task(
    agent="support-bot",
    input=task,
    permission_mode=None,           # use agent default
    thread_id="thread_demo_001",    # optional
)
```

## Authoring a New Scenario

1. `mkdir scenarios/<scenario-name>/`
2. Write `scenario.md` declaring the agent, mode, options, and the runtime behavior the scenario proves.
3. Write `task.json` (the value of `RunTaskRequest.input`).
4. Write `tools.md` listing each tool the scenario expects to be registered, with `ToolSpec` fields.
5. Write `expected.md` describing the expected trace events, output validation result, and any approval interrupts.

Scenarios are reusable: the same scenario can run against any agent that satisfies its tool and skill requirements.
