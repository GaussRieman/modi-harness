---
name: release-readiness
description: Decide go / no-go for a release based on CI, branch, and changelog state.
allowed-tools:
  - git_status
  - run_ci_status
risk_notes: []
tags:
  - release
---

# Release Readiness

## Procedure

1. Branch state:
   - Must be the configured release branch.
   - Must be clean (no uncommitted changes).
2. CI state:
   - All required jobs must be green on the latest commit.
   - Required job set is configured per project.
3. Changelog state:
   - Draft exists.
   - Draft references a tag matching the proposed release.
4. Decision:
   - `ready` if all three pass.
   - `blocked` otherwise; list specific reasons.

## Output

```yaml
readiness: ready | blocked
blockers:
  - kind: ci_failed | branch_dirty | changelog_missing | tag_mismatch
    detail: <human-readable>
```
