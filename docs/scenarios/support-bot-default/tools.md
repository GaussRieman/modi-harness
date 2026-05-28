# support-bot tools

Registering these tools in the harness lets the sample run.

## knowledge_search

```yaml
name: knowledge_search
description: Search the product knowledge base for documented answers.
risk_level: L1
side_effect: false
input_schema:
  type: object
  properties:
    query: { type: string }
    top_k: { type: integer, default: 3 }
  required: [query]
idempotent: true
```

## account_lookup

```yaml
name: account_lookup
description: Look up the current account state for the customer in the active conversation.
risk_level: L1
side_effect: false
input_schema:
  type: object
  properties:
    account_id: { type: string }
  required: [account_id]
idempotent: true
```

## ticket_create

```yaml
name: ticket_create
description: Open an internal follow-up ticket. Not visible to the customer.
risk_level: L3
side_effect: true
input_schema:
  type: object
  properties:
    summary: { type: string }
    severity: { type: string, enum: [low, medium, high] }
    related_account_id: { type: string }
  required: [summary, related_account_id]
idempotent: false
```

## escalate_to_human

```yaml
name: escalate_to_human
description: Hand off the conversation to a human support agent with a structured payload.
risk_level: L4
side_effect: true
input_schema:
  type: object
  properties:
    situation_summary: { type: string }
    account_id: { type: string }
    customer_message: { type: string }
    tried_steps: { type: array, items: { type: string } }
  required: [situation_summary, account_id, customer_message]
idempotent: false
```
