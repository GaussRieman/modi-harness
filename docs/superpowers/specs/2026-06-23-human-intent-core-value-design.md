# Human Intent Core Value Design

Date: 2026-06-23

## Decision

The core value of Modi Harness is:

> Autonomous agents, aligned with human intent.

The first principle is:

> Bounded autonomy within human intent.

This refines the earlier “human-centered” positioning. Human-centered does not
mean more human participation. It means the runtime preserves human intent while
reducing the need for human micromanagement.

## Why this matters

Governance alone can only simulate alignment. It can approve, deny, log, and
audit actions, but it does not by itself define what the agent is trying to
serve.

Strict alignment can also fail: if the runtime binds the agent too tightly to
human-written steps, the agent loses useful autonomy and becomes a scripted
workflow.

Modi Harness should hold the middle:

```text
human intent defines the field
agent autonomy explores within the field
alignment checks the boundary
governance proves what happened
```

## Message hierarchy

1. Core promise: Autonomous agents, aligned with human intent.
2. First principle: Bounded autonomy within human intent.
3. Product story: reduce human involvement in low-value details while
   strengthening alignment at goal, boundary, stage, responsibility, and
   outcome levels.
4. Supporting capabilities:
   - Human Intent Context
   - task and stage alignment
   - policy gates
   - pause/review/resume
   - checkpoints
   - action integrity
   - decision trails
   - trace and cost attribution

## Design implications

The long-term runtime should make Human Intent Context a first-class concept.
It should capture at least:

- goal and desired outcome;
- boundaries and non-goals;
- success criteria;
- current stage;
- responsibility owner;
- escalation preference;
- human decisions and corrections;
- active tradeoffs such as speed, quality, cost, and risk.

Human input should update this context, not merely create trace events.

## Consequences

- Governance language should be weakened in top-level positioning.
- Alignment language should be stronger, but not strict or micromanaging.
- Human-in-the-loop should remain a concrete mechanism, not the category.
- The README should emphasize autonomy and intent before approvals or audit.
- Architecture docs should describe Policy, Trace, and Output validation as
  support layers that preserve and prove alignment.
