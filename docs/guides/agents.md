# Defining Workflow Agents

Every `ModiAgent` owns at least one explicit Workflow. There is no standalone
execution path.

## Discovery manifest

An Agent package is discoverable when its directory contains `agent.toml`.
A trusted Python factory package uses:

```toml
factory = "agent:build_agent"
```

The manifest marks the directory as an Agent package and locates its factory.
The `ModiAgent` returned by that factory is the actual Agent definition. Direct
callers that pass `ModiAgent` objects to `ModiSession` do not need a manifest.

## Agent definition

```python
agent = ModiAgent(
    name="example",
    description="Example Workflow Agent",
    instruction="Global rules shared by every Node.",
    workflows=(workflow,),
    tools=tool_bindings,
    skills=skills,
    completion_validators=validators,
    permission_profile=permissions,
)
```

- Agent owns identity, global instruction, Workflows, trusted bindings, and
  authority.
- Workflow owns stable business phases.
- Autonomous Node owns one uncertain phase goal and completion boundary.
- Skill owns methodology.
- Tool owns executable work.
- Completion validator owns semantic proof the Harness can trust.

For a complete package, four-Node Workflow, execution examples, extension
procedure, and Trace inspection, see
[`agents/research_assistant/README.md`](../../agents/research_assistant/README.md).
