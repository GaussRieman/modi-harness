# Product Positioning

This document defines the public positioning for Modi Harness. It is the source
of truth for README copy, package descriptions, website language, and roadmap
framing.

## Category

**Human-in-the-Loop Agent Runtime**

Modi Harness is not positioned as a generic agent framework, prompt optimizer,
or token-efficiency library. It is the runtime layer for teams that want agents
to take real actions while keeping humans in control of consequential moments.

## Core promise

**Let agents act. Keep humans in control.**

Modi Harness lets teams give AI agents real capabilities without surrendering
control. When an action matters, the right person can step in, understand what
is about to happen, change the decision path when needed, and let the agent
continue.

## Buyer and trigger

Primary buyer: enterprise AI platform, infrastructure, and application teams.

Primary trigger: an agent is moving from read-only assistance into real
side-effectful work, such as calling internal tools, changing records, sending
messages, opening tickets, writing files, or coordinating multi-step workflows.

The buying question is not “can we build an agent?” It is:

> Can we let this agent act without losing control of responsibility,
> recoverability, and auditability?

## Narrative

Most teams face a bad choice: keep agents harmless, or give them power and hope
nothing goes wrong. Modi Harness creates a third path — agents that can work
independently while bringing people in exactly when their judgment matters.

The product story should emphasize confidence to delegate, not more control
for its own sake. The point is to let agents do more real work because the
runtime knows when to stop, who should decide, what context they need, and how
to continue afterward.

## Messaging pillars

### 1. Give agents room to work

Teams define where agents may act freely and where human judgment is required.
Routine work continues automatically; consequential actions pause at the policy
boundary.

### 2. Make human intervention count

Human review should happen at the moment of action, with enough context to make
a real decision. The long-term interaction is review, modify, approve, reject,
and resume — not a shallow confirmation dialog.

### 3. Continue with confidence

After a decision, the agent should continue from the same run instead of
starting over. The runtime should preserve execution state and maintain a
decision trail connecting agent intent, human judgment, tool execution, and
outcome.

## Technical proof points

Technical language should support the positioning, not replace it.

- Policy gates decide when human judgment is needed.
- Checkpoints make pause/resume reliable.
- Action integrity ensures the resumed action matches the approved action.
- Decision trails connect intent, reviewer action, execution, and outcome.
- Trace and cost attribution explain what happened and what it cost.
- Typed APIs, explicit contracts, and lean dependencies make the runtime
  friendly to both humans and coding agents.

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
> cost per successful governed task — not just count raw tokens.

Avoid leading with “token-efficient agent harness” unless the audience is
already inside the broader Modi cost-optimization platform narrative.

## Copy rules

Prefer:

- “Let agents act. Keep humans in control.”
- “Human-in-the-Loop Agent Runtime”
- “consequential actions”
- “pause, review, modify, approve, reject, resume”
- “decision trail”
- “confidence to delegate”

Avoid as the primary message:

- “AI-native”
- “token-efficient”
- “LangChain wrapper”
- “approval workflow”
- “governance framework”
- “audit/logging/permission framework”

Those terms may appear as supporting proof, but they should not carry the
product’s first impression.
