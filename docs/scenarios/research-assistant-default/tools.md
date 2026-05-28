# research-assistant tools

## web_search

```yaml
name: web_search
description: Search the web for candidate sources on a topic.
risk_level: L1
side_effect: false
input_schema:
  type: object
  properties:
    query: { type: string }
    top_k: { type: integer, default: 5 }
  required: [query]
idempotent: true
```

## fetch_url

```yaml
name: fetch_url
description: Fetch a URL and return its text content. Result is untrusted.
risk_level: L1
side_effect: false
input_schema:
  type: object
  properties:
    url: { type: string, format: uri }
  required: [url]
idempotent: true
```

## read_workspace_file

```yaml
name: read_workspace_file
description: Read a file already saved in the run workspace.
risk_level: L1
side_effect: false
input_schema:
  type: object
  properties:
    ref: { type: string }
  required: [ref]
idempotent: true
```

## write_draft

```yaml
name: write_draft
description: Write the draft briefing into the run workspace drafts/ directory.
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
