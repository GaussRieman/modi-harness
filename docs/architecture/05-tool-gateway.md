# Tool Gateway

Tool Gateway is the only execution path for model-requested tools.

See [`types-reference.md`](../types-reference.md) for `ToolSpec`, `RetryPolicy`, `ToolCallProposal`, `ToolCallRecord`.

## Chain

```text
tool call proposal
-> registry lookup            (unknown tool fails closed)
-> schema validation
-> agent / skill visibility check
-> denied-retry check
-> dispatch pre_tool_use hooks
-> Policy Gate
-> execute or interrupt
-> dispatch post_tool_use hooks
-> normalize result, attach trust annotation
-> audit
```

## Visibility

The runtime visibility intersection (`agent ∩ skill ∩ policy`) is performed by Context Manager when assembling the prompt. Tool Gateway re-checks at execution time as defense in depth.

## Result Normalization

Every tool result becomes a `ContextBlock` with `trust = untrusted` and an explicit `source_kind = "tool_result"`. Results above the configured size threshold are written to Workspace and represented by `workspace_ref`.

## Risk Levels

See [`types-reference.md`](../types-reference.md) for the `L0..L4` enum.

- `L0` compute
- `L1` read
- `L2` draft write
- `L3` business write
- `L4` external action

Risk level is declared on `ToolSpec`. Policy Gate maps level + mode → decision.

## Dry Run

When `permission_mode == "plan"` and `ToolSpec.dry_run_supported` is true, Tool Gateway invokes the tool's dry-run path (which must be side-effect-free by contract) and returns the proposed effect as a `would_do` payload.

## Rules

- Unknown tools fail closed.
- LangChain tools still pass through Modi policy before execution.
- Tool results are untrusted observations.
- Hook blocks return structured errors equivalent to a deny.
- Prompt-injection findings are surfaced, never executed.
- Denied-retry check runs before execution; the same `fingerprint` is rejected without consulting Policy Gate.
- Idempotency: when `idempotent=true`, repeated calls within a run with the same fingerprint return the cached result rather than re-executing.

## Boundaries

- Decisions: Policy Gate.
- Hook execution: Hook System.
- Persistence: Workspace Manager.
- Trace: Trace Recorder.
- Repair on malformed input: Runtime Adapter.
