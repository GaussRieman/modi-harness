# Expected Behavior — case-reviewer

A successful run on `task.json` should:

1. Read case `case_2026_0451`.
2. Read all attached evidence items.
3. Optionally `search_law` for unclear classifications.
4. Produce a structured draft with the five required fields.
5. `write_draft` is L2 + `review_required: true` per the agent's permission profile, so the draft enters `needs_review` rather than `final`.

Output should validate against the structured contract:

- `summary`: paragraph
- `issues`: list of { description, case_section, severity, labels }
- `evidence_gaps`: list of { fact, reason, case_section }
- `risks`: list of { label, severity, rationale }
- `next_actions`: list of strings addressed to a human

Trace should include:

- `policy_decision` showing `write_draft` routed to `require_review`
- `output_validation` with status `needs_review`
- `memory_selection` showing any `project` memory pinned to `case_2026_0451`
