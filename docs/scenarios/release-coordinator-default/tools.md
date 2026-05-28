# release-coordinator tools

## git_status

```yaml
name: git_status
description: Report current branch, dirty state, and HEAD commit.
risk_level: L1
side_effect: false
input_schema:
  type: object
  properties: {}
idempotent: true
```

## read_changelog_draft

```yaml
name: read_changelog_draft
description: Read the proposed changelog draft from the repo.
risk_level: L1
side_effect: false
input_schema:
  type: object
  properties:
    path: { type: string, default: CHANGELOG.next.md }
idempotent: true
```

## run_ci_status

```yaml
name: run_ci_status
description: Fetch the latest CI run for the current commit.
risk_level: L1
side_effect: false
input_schema:
  type: object
  properties:
    commit_sha: { type: string }
  required: [commit_sha]
idempotent: true
```

## jira_create_release_ticket

```yaml
name: jira_create_release_ticket
description: File a release approval ticket in JIRA.
risk_level: L3
side_effect: true
input_schema:
  type: object
  properties:
    release_tag: { type: string }
    notes: { type: string }
    blockers: { type: array, items: { type: object } }
  required: [release_tag]
idempotent: false
```

## send_slack_release_summary

```yaml
name: send_slack_release_summary
description: Post a release readiness summary to the release channel.
risk_level: L4
side_effect: true
input_schema:
  type: object
  properties:
    channel: { type: string }
    release_tag: { type: string }
    summary: { type: string }
  required: [channel, release_tag, summary]
idempotent: false
```
