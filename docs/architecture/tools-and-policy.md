# Tools and Policy

## Tool assembly

`ToolRegistry` stores `ToolSpec`, handler, and optional dry-run handler.
`ModiHarness` registers builtin and plugin-contributed kernel Tools;
`ModiSession` merges them with Agent Tools and generated subagent Tools.

Context Manager exposes only the intersection allowed by Agent declaration,
Skill restrictions, protocol state, builtin visibility, and Policy filtering.

## Execution chain

Every model-requested operation enters `ToolGateway`:

```text
registry lookup
-> JSON Schema validation
-> Agent visibility check
-> denied-retry guard
-> pre-tool hooks
-> Policy decision
-> execute / simulate / interrupt / deny
-> post-tool hooks
-> normalized untrusted result
```

Independent non-approval Tool calls from one model turn execute as a batch in
stable order. Errors are isolated per call. Large results are written to the
workspace and represented by references.

## Policy

`PolicyGate` is the single decision point for Tool calls, Memory writes, and
output finalization. Decisions combine:

- Tool risk level (`L0`–`L4`);
- run permission mode;
- Agent deny/review/preauthorization lists;
- merged user/project permission settings;
- rule packs;
- prior denied-action fingerprints.

Product modes are `auto`, `preview`, and `trust`. Legacy `ask`, `plan`, and
`bypass` names normalize to those modes with deprecation warnings. `trust`
requires `MODI_ALLOW_TRUST=1`.

External Tool results and workspace references are observations, not
instructions. `ModelAdapter` wraps untrusted material before provider calls;
the Output Controller rejects leaked untrusted tags and common injection
artifacts.

## Source entry points

- `tools/registry.py`, `tools/gateway.py`, `tools/builtin.py`
- `policy/gate.py`, `policy/modes.py`, `policy/permissions.py`
- `policy/rule_packs.py`
- `models/adapter.py`

