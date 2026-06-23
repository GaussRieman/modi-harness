# First Principles

Modi Harness exists to give agents useful autonomy without letting that
autonomy drift away from human intent.

The core principle is:

> **Bounded autonomy within human intent.**

Humans should not have to micromanage every task step. Agents should be free to
plan, explore, call tools, recover from intermediate failures, and produce
artifacts. But that freedom must stay inside an intent field defined by human
goals, boundaries, success criteria, responsibilities, and stage-level
judgment.

## The tension

Agent systems fail in two opposite ways:

- Too much governance: the agent becomes a permission workflow with a model
  attached.
- Too much autonomy: the agent completes work while quietly drifting away from
  the human purpose of the task.

Modi Harness should hold the productive middle:

```text
human intent defines the field
agent autonomy explores within the field
alignment checks the boundary
governance proves what happened
```

## Operating principles

### 1. Align on intent, not every step

Human-centered does not mean constant human involvement. The human should align
the goal, constraints, success criteria, and responsibility behind the work.
The agent should own the path whenever the path is inside those boundaries.

### 2. Preserve autonomy inside clear boundaries

The runtime should give agents enough room to be useful: decompose tasks, pick
tools, revise intermediate plans, handle failures, and create artifacts. Human
intent should act as a compass and boundary, not a script.

### 3. Escalate at judgment points

Human participation is most valuable at judgment points:

- the goal is ambiguous;
- a phase is about to change from exploration to execution;
- a tool call would create an external side effect;
- a decision implies responsibility or commitment;
- the agent proposes work outside the declared intent field;
- the user’s feedback changes the boundary of the task.

Approval is only one form of judgment. Review, correction, redirection,
clarification, and scope change are equally important.

### 4. Human input must update the run

Human input is not merely a log entry. It should update the runtime’s active
understanding of the task: goal, boundary, stage, priority, responsibility, and
acceptable tradeoffs.

The agent should continue from that updated understanding instead of restarting
or treating the human response as isolated feedback.

### 5. Governance is proof, not the product soul

Policy gates, permissions, approval prompts, trace, audit logs, and output
validation are necessary. But they are supporting mechanisms. Their purpose is
to preserve and prove alignment, not to replace alignment with control.

## Design implication

The long-term runtime should make **Human Intent Context** a first-class
concept. That context should include at least:

- goal and desired outcome;
- boundaries and non-goals;
- success criteria;
- current stage;
- responsibility owner;
- escalation preference;
- human decisions and corrections;
- active tradeoffs such as speed, quality, cost, and risk.

The agent should not be bound to human-written steps. It should be bound to the
human intent field.
