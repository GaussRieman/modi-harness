# Plugins

Plugins may contribute kernel Tools and fully constructed `ModiAgent` values.
Every contributed Agent must contain at least one explicit Workflow.

Canonical declarative Agents use a package directory:

```text
agents/
  my-agent/
    agent.toml
    workflows/
      default.yaml
    skills/
```

Trusted executable packages instead use an exact manifest containing only
`factory = "module:function"`. User directories never execute factories.

Discovery merges plugin, configured project, conventional project, user, and
explicit directory sources. Ambiguous unqualified names fail; qualified source
names resolve deterministically.
