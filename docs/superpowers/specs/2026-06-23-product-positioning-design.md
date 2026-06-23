# Product Positioning Design

Date: 2026-06-23

## Decision

Modi Harness is positioned as a **Human-Centered Agent Runtime**.

The core message is:

> Build agents around human intent.

This supersedes the earlier intermediate positioning:

> Let agents act. Keep humans in control.

It also replaces the previous primary positioning:

> An AI-native agent harness, engineered for token efficiency.

## Why change

The previous message was accurate in pieces but weak as a product promise. It
led with implementation taste and optimization claims before explaining why a
team would urgently need the project.

The stronger buying trigger is when agents move from harmless assistance into
real side-effectful work. At that point, enterprise teams need more than a
human-in-the-loop approval mechanism. They need a runtime that keeps agents
aligned with human intent, boundaries, judgment, responsibility, and context.

## Target audience

Primary audience: enterprise AI platform, infrastructure, and application
teams.

Primary adoption shape: embedded Python runtime on top of LangChain and
LangGraph.

Primary trigger: agents are about to call real tools, change records, write
files, send messages, open tickets, or coordinate workflows where responsibility
and recoverability matter.

## Message hierarchy

1. Core promise: Build agents around human intent.
2. Category: Human-Centered Agent Runtime.
3. Product story: teams should not have to choose between harmless agents and
   risky autonomy.
4. Capability pillars:
   - Start from human intent.
   - Give agents room to work.
   - Make human judgment count.
   - Continue with confidence.
5. Technical proof:
   - policy gates
   - approvals and human review as one runtime mechanism
   - checkpointed pause/resume
   - action integrity
   - traces and decision trails
   - cost attribution per governed task

## Scope honesty

The README should present the long-term product direction clearly while naming
the current implementation honestly.

Current implementation includes governed execution, approval interrupts,
checkpointed resume, workspaces, memory, output validation, and structured
traces.

Near-term direction includes editable reviews, stronger action integrity,
richer decision trails, clearer cost attribution per governed task, and better
ways to keep agents aligned with declared human goals and boundaries.

## Consequences

- README and package metadata should no longer lead with AI-native or token
  efficiency.
- Human-in-the-loop becomes a concrete capability, not the product category.
- Human-aligned becomes the design goal: agents should stay attached to the
  human purpose of the task, not merely the literal next tool call.
- AI-native becomes a design principle: human judgment belongs inside the agent
  execution loop, not outside as a bolted-on approval form.
- Token efficiency becomes a proof point: governance and trace make it possible
  to optimize cost per successful governed task.
- Project language should avoid sounding like a generic audit/logging/permission
  framework. Those are ingredients; the product value is confidence to delegate
  real action to agents.
