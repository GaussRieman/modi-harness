# Output and Hooks

## Output lifecycle

Free-form output is the default. A structured `OutputContract` may require a
JSON Schema, named fields, citations, risk labels, forbidden patterns, or human
review.

Structured Agents receive the synthetic `submit_output` Tool. The graph
intercepts that call as a typed draft; it is not dispatched through an external
handler. During finalization, all other Tools are hidden.

`OutputController` is a pure validator. It checks schema and required fields as
well as forbidden content, prompt-injection artifacts, security claims, and
claims that contradict denied actions. Outcomes are:

- `validated`: finish and persist the output;
- `needs_review`: preserve the draft and pause;
- `rejected`: append precise repair feedback and retry within budget.

Successful structured output is written to the run drafts and recorded by an
`output_submitted` trace event.

## Hooks

`HookRegistry` merges configured Hook specifications. `HookDispatcher` runs
matching Python or shell callbacks with controlled payload and environment;
shell callbacks also have a timeout. Hook results normalize to `proceed`,
`block`, or permitted `redirect`.

Hooks observe lifecycle events around user input, model calls, Tools, Memory,
and output. A blocking Hook can stop the current action but cannot grant
permission or bypass Policy. Tool Gateway owns pre/post Tool Hook dispatch;
graph nodes dispatch the remaining lifecycle events.

Hook executions and failures are trace events. Hook output is bounded and
treated according to its event boundary.

## Source entry points

- `output/controller.py`
- `graph/nodes.py`
- `hooks/registry.py`, `hooks/dispatcher.py`
- `tools/gateway.py`
