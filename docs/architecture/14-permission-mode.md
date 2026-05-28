# Permission Mode

Permission Mode is a run-scoped switch that shifts default Policy Gate behavior. It does not change what is risky; it changes how aggressively the harness should pause for human input.

The same agent, same skills, same tools can run in different modes for different deployments without changing any agent or skill file.

## Modes

- `ask`
  Default. Policy defaults apply as written: L0/L1 allow, L2 workspace-only, L3/L4 require approval. Anything risky pauses.

- `auto`
  Reduces approval friction for trusted environments. L3 actions that match a preauthorized list in settings auto-allow; everything else still requires approval. L4 always requires approval.

- `plan`
  No side effects. All L2+ actions are rewritten to `require_review` with status `draft`. Useful for "explain what you would do" runs and for evaluating an agent before granting execution rights.

- `bypass`
  All actions except an explicit deny list are allowed without prompting. Reserved for sandboxed CI runs and offline replays. Never the default. Requires explicit opt-in per run.

## Decision Matrix

| Risk | ask | auto | plan | bypass |
|------|-----|------|------|--------|
| L0 compute | allow | allow | allow | allow |
| L1 read | allow | allow | allow | allow |
| L2 draft write (workspace) | allow | allow | review | allow |
| L2 outside workspace | approval | approval | review | allow* |
| L3 business write | approval | preauthorized → allow, else approval | review | allow* |
| L4 external action | approval+audit | approval+audit | review | allow* |
| denied-retry | deny | deny | deny | deny |
| destructive without authorization | deny | deny | deny | deny |

`*` bypass still respects the static deny rules: denied-retry, destructive-without-authorization, security abuse.

## Selection

Resolution order, first match wins:

1. `RunTaskRequest.permission_mode` (per-run override).
2. Agent `permission_profile.mode` (per-agent default).
3. `Settings.MODI_PERMISSION_MODE` (deployment default).
4. Fallback: `ask`.

## Mode Changes Mid-Run

- A run cannot self-elevate its mode. Only the Harness API caller can change mode, and only at `resume_task`.
- Downgrading (`auto` → `ask`, `bypass` → `ask`) is always allowed.
- Upgrading (`ask` → `auto`, anything → `bypass`) requires explicit caller intent and is recorded as a trace event.

## Rules

- Permission Mode is metadata on Policy Gate input, not a separate decider.
- Policy Gate must produce the same `PolicyDecision` shape for all modes.
- `plan` mode never executes tool side effects; tools may be invoked in dry-run form if they declare `dry_run_supported`, otherwise they are skipped with a `review_required` decision and a `would_do` payload.
- `bypass` does not disable Output Controller, Trace Recorder, or denied-retry guard.
- Hooks observe mode through `payload.permission_mode` and can refuse to operate in `bypass`.

## Boundaries

- Permission Mode does not redefine risk levels.
- Permission Mode does not change Trust boundaries.
- Permission Mode does not silence approvals already requested before the mode change.
- Memory of past denials persists across mode changes within the same run.
