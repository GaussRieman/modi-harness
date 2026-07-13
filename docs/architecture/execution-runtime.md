# Execution Runtime

`ModiSession` builds one `WorkflowSessionAdapter` with the Agent registry,
Action Gateway, Workspace, Memory Store, and caller-provided checkpointer.

For a new run the adapter deterministically selects a Workflow, validates its
input, builds a versioned execution contract, and starts `WorkflowRuntime`.
Operation Nodes dispatch once through the Action Gateway. Autonomous Nodes run
one AgentLoop step at a time until the Brain proposes `complete_node`, a wait,
or failure.

Checkpoint snapshots contain the selected Agent and Workflow plus plain
Workflow state and trace data. Resume reconstructs the execution contract and
rejects changed definition or dependency fingerprints.

Source entry points:

- `api/session.py`
- `workflow/session.py`, `workflow/runtime.py`, `workflow/contract.py`
- `loop/runtime.py`, `brain/default.py`, `brain/model.py`
- `actions/gateway.py`, `tools/gateway.py`
