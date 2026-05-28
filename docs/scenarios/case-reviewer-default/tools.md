# case-reviewer tools

## read_case

```yaml
name: read_case
description: Read the case file structure and content.
risk_level: L1
side_effect: false
input_schema:
  type: object
  properties:
    case_id: { type: string }
  required: [case_id]
idempotent: true
```

## read_evidence

```yaml
name: read_evidence
description: Read evidence items attached to a case.
risk_level: L1
side_effect: false
input_schema:
  type: object
  properties:
    case_id: { type: string }
    item_id: { type: string }
  required: [case_id]
idempotent: true
```

## search_law

```yaml
name: search_law
description: Search internal legal classification reference.
risk_level: L1
side_effect: false
input_schema:
  type: object
  properties:
    query: { type: string }
  required: [query]
idempotent: true
```

## write_draft

```yaml
name: write_draft
description: Write the review draft into the run workspace for human reviewer.
risk_level: L2
side_effect: true
input_schema:
  type: object
  properties:
    name: { type: string }
    content: { type: object }
  required: [name, content]
idempotent: false
dry_run_supported: true
```
