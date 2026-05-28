# Expected Behavior — release-coordinator-default

A successful run on this scenario should:

1. `git_status()` and `run_ci_status(commit_sha=<from git_status>)` execute without prompt (L1, allowed under all modes).
2. `read_changelog_draft()` executes; content enters context wrapped as `<untrusted source_kind="tool_result">`.
3. `release-readiness` skill produces a readiness decision.
4. If `ready`:
   - `jira_create_release_ticket` (L3) is **preauthorized** in this agent's profile under `auto` mode → allowed without prompting.
   - `send_slack_release_summary` (L4) is in `review_required` → interrupts with `pending_approval` regardless of mode.
5. If `blocked`: skip both creation tools; surface blockers in structured output.

## Output Fields

- `release_tag`
- `readiness`: `ready` | `blocked`
- `blockers`: list per `release-readiness` skill schema
- `notes`: customer-facing notes from `changelog-style` skill
- `next_action`: string telling the human what to confirm

## Trace Should Include

- `run_start`
- `hook_dispatch` for `pre_tool_use` when a project-level hook is configured (e.g. require JIRA epic id)
- `policy_decision` showing `jira_create_release_ticket` → `allow` (preauthorized) and `send_slack_release_summary` → `require_review`
- `denial` events if the model retries a denied tool call unchanged (denied-retry guard)
- `output_validation` with status `validated` or `needs_review` (the latter when Slack summary still pending approval at end of run)
- `run_end`

## Rule Pack Behavior

With `coding` rule pack enabled:

- Any model-proposed git mutation tool (e.g. `git_push`, `git_tag`) is denied immediately, even if accidentally registered.
- This is enforced at Policy Gate matcher level, not at agent-tool-list level — agents that never declare such tools are still protected from model hallucination.
