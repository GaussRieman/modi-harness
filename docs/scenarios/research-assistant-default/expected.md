# Expected Behavior — research-assistant-default

## Plan mode run

When invoked with `permission_mode="plan"`:

- `web_search` and `fetch_url` do not declare `dry_run_supported=true`, so Policy Gate rewrites them to `require_review` with a `would_do` payload describing the intended query / URL.
- `write_draft` declares `dry_run_supported=true`; Tool Gateway invokes the dry-run path and returns the proposed draft body.
- Final output has `confidence: "unverified"`.
- `key_findings` may be empty or contain placeholder findings tied to plan items.
- `OutputController` returns `needs_review` because the contract requires citations and none exist.

## Ask mode run

When invoked with `permission_mode="ask"` (agent default):

- `web_search` and `fetch_url` execute freely (L1).
- Each fetched page is wrapped as `<untrusted source_kind="tool_result">` in the prompt by Model Adapter.
- The model uses `source-evaluation` to grade sources and `briefing-structure` to assemble output.
- `write_draft` (L2, target `<workspace>/drafts/`) is allowed because target is inside the run workspace.
- `OutputController`:
  - `validated` when contract checks pass
  - `rejected` with `citation.missing` if any `key_finding` lacks a citation key
  - `needs_review` if `confidence: "high"` but evidence is only commentary-grade

## Trace Should Include

- `run_start`
- `memory_selection` (empty if no prior `project` memory tagged with `research`/`briefing`)
- `tool_call` for each `web_search` and `fetch_url`, each result with `trust.trust_level="untrusted"`
- `context_built` event with per-block trust summary
- `output_validation` with stable issue codes when rejected
- `run_end`
