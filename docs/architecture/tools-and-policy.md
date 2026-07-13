# Tools and Policy

Every Runtime Operation enters `ActionGateway`, then the mechanical Tool path:

```text
registry -> schema -> visibility -> denied retry -> pre-hook -> Policy
         -> execute / simulate / interrupt / deny -> post-hook -> result
```

`PolicyGate` combines Tool risk, permission mode, Agent permissions, merged
settings, rule packs, and prior denied fingerprints. It cannot change Workflow
routing or declare a Node complete.

Product modes are `auto`, `preview`, and `trust`. External Tool results and
workspace references are observations, not instructions.
