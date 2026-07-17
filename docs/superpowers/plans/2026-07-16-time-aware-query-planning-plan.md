# Time-Aware Query Planning Implementation Plan

## Goal

Implement the approved design in
`docs/superpowers/specs/2026-07-16-time-aware-query-planning-design.md` so every
Research Assistant Web query uses a fresh current-time token, comparison
queries preserve entity identity and candidate coverage, and tasks cannot skip
evidence verification before recording a Finding.

## 1. Generic fresh-output prerequisite

- Extend `ToolSpec` and `OperationAdapter` with optional generic prerequisite
  metadata: issuer Operation, input argument, issuer output field, issued-at
  field, TTL, and single-use behavior.
- Include prerequisite metadata in execution-contract snapshots.
- Populate adapter metadata from trusted Tool specs in WorkflowSession.
- Before preparing any operation invocation, resolve the referenced issuer
  output from same-run persisted InvocationRecords, validate TTL, and reject a
  token already used by another invocation.
- Add Runtime tests for missing, cross-run, expired, reused, and restored tokens.

## 2. Current-time Operation and quick lookup

- Add `get_current_time` returning UTC and Asia/Shanghai timestamps, current
  date/year, a random token, issued time, and expiry.
- Bind/export the Operation and mark it non-idempotent.
- Add a deterministic `current_time` Operation node before quick lookup search.
- Require `time_token` on both Web search tools.
- Update quick-lookup integration tests to assert time precedes search.

## 3. Query-planning Skill and structured search

- Add `skills/query-planning/SKILL.md` with entity/alias extraction, exact-name
  quoting, current-year use, one entity per search item, and gap-driven follow-up
  rules.
- Load both `query-planning` and `web-research` Skills.
- Replace `public_web_search.queries` with bounded structured `searches` entries.
- Normalize entity phrases compactly so `Model Y`, `ModelY`, and `Model-Y` are
  equivalent while standalone `Y` has no weight.
- Rank each search item independently and allocate fetch attempts round-robin.
- Add Model Y versus Model 3 and Tesla versus Xiaomi coverage tests.

## 4. Run-scoped search and verification IDs

- Add random `search_id` and bounded `operation_summary` to search outputs.
- Replace process-global observed-URL provenance checks with same-run StepRecord
  resolution in Workflow Runtime.
- Require `verify_claim_evidence.search_ids`; enforce exact coverage of every
  usable URL from all searches for the task, including an empty-items path when
  no usable URL exists.
- Return `verification_id`, covered search IDs, evaluated URLs, and normalized
  evidence.
- Require `record_research_finding.verification_id` except for
  `unverifiable_flag`; validate current generation and evidence equality.
- Add tests for omitted URLs, unknown IDs, zero-source verification, follow-up
  invalidation, and cumulative initial-plus-follow-up coverage.

## 5. Workflow, instructions, and trace

- Update deep-research capability tools and sequence wording to require
  time -> search -> verify -> Finding.
- Update Agent and both Skills with token and ID protocol rules.
- Include bounded `operation_summary` in `operation_completed` trace events;
  exclude source excerpts.
- Update architecture documentation.

## 6. Verification

- Run focused Runtime, research tool, research workflow, trace, and CLI tests.
- Run the complete test suite.
- Run Ruff and mypy.
- Inspect the final diff for unrelated changes and preserve existing user edits.
