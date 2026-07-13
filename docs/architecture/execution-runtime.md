# Execution Runtime

`ModiSession` builds one `WorkflowSessionAdapter` with the Agent registry,
Action Gateway, Workspace, Memory Store, and caller-provided checkpointer.

For a new run the adapter deterministically selects a Workflow, validates its
input, builds a versioned execution contract, and starts `WorkflowRuntime`.
Operation Nodes dispatch once through the Action Gateway. Autonomous Nodes run
one AgentLoop step at a time until the Brain proposes `complete_node`, a wait,
or failure.

Agent Skills are injected into each autonomous Brain context. A Node narrows
the available tool set, while its completion schema and optional semantic
validator govern only the result boundary, not the internal solution path.
When information is missing, the Brain may call `request_user_input`; this maps
to the existing structured ask and waiting checkpoint rather than a
RuntimeOperation. Human input is then returned to the same Node attempt.

Model providers receive a hard request timeout and have SDK retries disabled.
The Harness-level model adapter is the only retry owner.

A definite Tool failure inside an autonomous Node is Step evidence, not an
automatic Workflow failure. AgentLoop returns the error to the Brain so it may
revise the local plan; step-budget exhaustion fails the Node. Operation Nodes
remain deterministic and follow their declared failure transition directly.

An individual Tool may declare `max_calls_per_node`. The Brain sees only Tools
whose per-input-round budget remains, while WorkflowRuntime checks the same
execution-contract value before creating an invocation. Human input resets the
round-local count so newly supplied identifiers can be researched. This
progress bound prevents one unproductive Operation from consuming the entire
Node `max_steps` budget.

Provider parallel tool calls are disabled because one AgentLoop Step owns one
RuntimeOperation. If a provider still emits multiple proposals, the planner
selects one proposal for that Step, preferring a permitted argument fingerprint
not already tried in the current input round. The next Brain turn reconsiders
the remaining work against the committed result.

Checkpoint snapshots contain the selected Agent and Workflow plus plain
Workflow state and trace data. Resume reconstructs the execution contract and
rejects changed definition or dependency fingerprints.

Source entry points:

- `api/session.py`
- `workflow/session.py`, `workflow/runtime.py`, `workflow/contract.py`
- `loop/runtime.py`, `brain/default.py`, `brain/model.py`
- `actions/gateway.py`, `tools/gateway.py`

See the [Research Assistant package guide](../../agents/research_assistant/README.md)
for a complete four-autonomous-Node example.
