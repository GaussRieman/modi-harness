# Product Positioning

This document defines the public positioning for Modi Harness. It is the source
of truth for README copy, package descriptions, website language, and roadmap
framing.

## Category

**Human-Centered Agent Runtime**

Modi Harness is not positioned as a generic agent framework, prompt optimizer,
human-in-the-loop approval system, or token-efficiency library. It is the
runtime layer for teams that want agents to act with meaningful autonomy while
staying aligned with human intent, judgment, responsibility, and context.

## Core promise

**Autonomous agents, aligned with human intent.**

Modi Harness lets teams give AI agents real capabilities without forcing people
to micromanage every step. Humans define the intent field: goals, boundaries,
success criteria, responsibilities, and stage-level judgment. Agents remain free
to find the path inside that field.

## Buyer and trigger

Primary buyer: enterprise AI platform, infrastructure, and application teams.

Primary trigger: an agent is moving from read-only assistance into real
side-effectful work, such as calling internal tools, changing records, sending
messages, opening tickets, writing files, or coordinating multi-step workflows.

The buying question is not “can we build an agent?” It is:

> Can we let this agent work independently without drifting away from human
> intent, responsibility, and acceptable risk?

## Narrative

Most teams face a bad choice: keep agents harmless, or give them power and hope
nothing goes wrong. Modi Harness creates a third path — agents that can act
with bounded autonomy inside a human intent field.

The product story should emphasize confidence to delegate, not more control for
its own sake. The point is to reduce human involvement in low-value details
while strengthening alignment at the level that matters: goal, stage, boundary,
responsibility, and outcome.

## Messaging pillars

### 1. Align on intent, not every step

Agents should not merely execute prompts, and humans should not have to script
every move. The runtime should make the human goal, boundary, responsibility,
and success criteria explicit enough for the agent to act independently.

### 2. Preserve autonomy inside clear boundaries

Teams define the intent field. Inside it, agents should plan, explore, call
tools, recover from intermediate failures, and create artifacts without constant
supervision. Boundaries guide autonomy; they do not turn the agent into a
scripted workflow.

### 3. Escalate at judgment points

Human participation should happen when judgment matters: ambiguous goals, phase
changes, responsibility shifts, external side effects, or proposed actions
outside the declared intent field. The long-term interaction is clarify,
review, redirect, modify, approve, reject, and resume.

### 4. Let human input update the run

Human input should change the runtime’s active understanding of the task, not
just produce an audit event. After a correction or decision, the agent should
continue from the same run with updated intent, boundaries, and stage context.

## Technical proof points

Technical language should support the positioning, not replace it.

- Human Intent Context captures the goal, boundaries, stage, responsibility,
  success criteria, and human corrections behind a run.
- Policy gates decide when human judgment is needed.
- Checkpoints make pause/resume reliable.
- Action integrity ensures the resumed action matches the approved action.
- Decision trails connect intent, stage, reviewer action, execution, and
  outcome.
- Trace and cost attribution explain what happened and what it cost.
- Typed APIs, explicit contracts, and lean dependencies make the runtime
  friendly to both humans and coding agents.

## How to talk about human-in-the-loop

Human-in-the-loop is a capability, not the category.

Use it when describing concrete pause/review/resume mechanics. Do not use it as
the primary positioning, because it sounds like an approval workflow bolted onto
an agent. Modi Harness is broader: it is about centering the whole runtime on
human intent while preserving agent autonomy.

## How to talk about human-aligned

“Human-aligned” is the deeper design goal. Use it to describe runtime behavior
that keeps agents attached to the human purpose of the task without binding
them to human-written step-by-step instructions.

The concrete proof is not a vague safety claim; it is visible in declared
boundaries, policy gates, review moments, checkpoints, decision trails, output
validation, and traceable context.

## How to talk about governance

Governance is the proof layer, not the product soul.

Policy, permission, approval, audit, and trace are necessary because agents
touch real systems. But they should be framed as mechanisms that preserve and
prove alignment. Avoid making Modi Harness sound like a compliance wrapper.

## How to talk about AI-native

“AI-native” is not the headline. It is a design principle:

> Human judgment is part of the agent execution loop, not an approval form
> bolted onto the outside.

Use AI-native to explain why the runtime is built around structured state,
tool proposals, checkpoints, policy decisions, traceable context, and agent-
readable contracts.

## How to talk about token efficiency

Token efficiency is not the primary category claim. It is an operational proof
point that becomes credible when tied to trace and governance.

The preferred framing is:

> Once the runtime can explain every consequential step, teams can optimize
> cost per successful aligned task — not just count raw tokens.

Avoid leading with “token-efficient agent harness” unless the audience is
already inside the broader Modi cost-optimization platform narrative.

## Copy rules

Prefer:

- “Autonomous agents, aligned with human intent.”
- “bounded autonomy within human intent”
- “Human-Centered Agent Runtime”
- “human-aligned runtime”
- “intent field”
- “align on intent, not every step”
- “escalate at judgment points”
- “decision trail”
- “confidence to delegate”

Avoid as the primary message:

- “AI-native”
- “token-efficient”
- “human-in-the-loop”
- “LangChain wrapper”
- “approval workflow”
- “governance framework”
- “audit/logging/permission framework”
- “strict human control”

Those terms may appear as supporting proof, but they should not carry the
product’s first impression.
