---
name: escalation-policy
description: When and how to escalate to a human agent, with the required handoff payload.
allowed-tools:
  - escalate_to_human
risk_notes:
  - Escalation is review-required; never bypass.
tags:
  - policy
  - support
---

# Escalation Policy

Escalate when at least one of these holds:

- the customer explicitly asks for a human
- the issue involves billing disputes, refunds, or chargebacks
- the customer reports a safety, security, or privacy concern
- the customer has been waiting more than 24 hours per the conversation thread
- the issue is outside documented product behavior and you cannot find a knowledge match

## Handoff Payload

Always pass the following to `escalate_to_human`:

- one-paragraph situation summary, neutral wording
- account id (from `account_lookup`)
- the customer's latest verbatim message
- what you have already tried
- any memory of past escalations on this thread

## Constraints

- Do not escalate twice on the same thread within a single run.
- Do not promise a callback time; the human queue owns that.
