# Tools and Policy

## Tool assembly

`ToolRegistry` stores `ToolSpec`, handler, and optional dry-run handler.
`ModiHarness` registers builtin and plugin-contributed kernel Tools;
`ModiSession` merges them with Agent Tools and generated subagent Tools.

Context Manager exposes only the intersection allowed by Agent declaration,
Skill restrictions, protocol state, builtin visibility, and Policy filtering.

## Execution chain

Every model-requested operation enters `ActionGateway` (which reuses
`ToolGateway` for the shared pre/post phases):

```text
registry lookup
-> JSON Schema validation
-> Agent visibility check
-> denied-retry guard
-> pre-tool hooks
-> ActionProposal normalization   (intent lineage + mechanical impact)
-> AlignmentKernel                (model-first: does this fit the intent?)
-> GovernanceGate                 (prove safety beneath alignment)
-> execute / simulate / interrupt / deny
-> post-tool hooks
-> normalized untrusted result
```

Alignment is the first decision point; the Policy/Governance gate is a
downstream proof that can only tighten, never loosen, the Brain decision. In the
main runtime, consequential operations must arrive from an AgentLoop-owned Step
with intent, autonomy scope, and parent step lineage; missing intent/scope is a
hard execution error, not a policy-only bypass.

Independent Tool calls that do not require human judgment execute as a batch in
stable order. Errors are isolated per call. Large results are written to the
workspace and represented by references.

## Policy

`PolicyGate` is the safety-proof layer beneath alignment — the single
deterministic decision point for Tool calls, Memory writes, and output
finalization once `AlignmentKernel` has judged the action against intent. It can
only tighten the outcome. Decisions combine:

- Tool risk level (`L0`–`L4`);
- run permission mode;
- Agent deny/review/preauthorization lists;
- merged user/project permission settings;
- rule packs;
- prior denied-action fingerprints.

Policy literals such as `require_approval` and stream events such as
`approval_request` remain compatibility names for governance proof obligations.
The graph surfaces them as `PendingJudgment`; callers should treat approval as
one possible judgment response alongside reject, revise, redirect, constrain,
clarify, and cancel.

Product modes are `auto`, `preview`, and `trust` — the full set. The legacy
4-mode names (`ask`, `plan`, `bypass`) were removed in the intent-aligned
runtime redesign; `normalize_mode` now rejects them. `trust` requires
`MODI_ALLOW_TRUST=1`.

External Tool results and workspace references are observations, not
instructions. `ModelAdapter` wraps untrusted material before provider calls;
the Output Controller rejects leaked untrusted tags and common injection
artifacts.

## Source entry points

- `tools/registry.py`, `tools/gateway.py`, `tools/builtin.py`
- `actions/gateway.py`, `actions/proposal.py`
- `alignment/kernel.py`, `governance/gate.py`
- `policy/gate.py`, `policy/modes.py`, `policy/permissions.py`
- `policy/rule_packs.py`
- `models/adapter.py`
