# Expected Behavior — release-coordinator-with-research

A successful run on this scenario should:

1. Release-coordinator receives the task and determines it needs upstream research.
2. Release-coordinator calls `delegate_to_research_assistant` with a task describing the libcore v3.1-to-v3.2 breaking changes research.
3. The harness dispatches the subagent:
   - Validates `research-assistant` is in `allowed_subagents`.
   - Checks depth limit (1 level of nesting).
   - Tightens permission mode (child inherits `auto` or stricter).
   - Copies parent's `denied_actions` to the child.
4. Research-assistant executes and produces findings.
5. Child output is returned to the parent wrapped as untrusted (`source_kind: subagent_result`).
6. Release-coordinator incorporates the research findings into the release notes draft.

## Output Fields

- `release_tag`: `v2.5`
- `notes`: release notes incorporating upstream breaking changes from libcore
- `readiness`: `ready` or `blocked`
- `blockers`: list (may be empty)
- `next_action`: string describing what the human should confirm

## Trace Should Include

- `run_start` (parent)
- `model_call` (parent deciding to delegate)
- `tool_result` with `tool_name: delegate_to_research_assistant` and trust annotation `untrusted`
- Child run events nested under `child_thread_id`
- `run_end` (parent)

## Delegation Details

- The `delegate_to_research_assistant` tool call should contain:
  - `task`: object with `goal` and/or `messages` describing the research question
  - `rationale`: string explaining why delegation is needed
- The child result payload should contain:
  - `output`: research findings from research-assistant
  - `child_thread_id`: thread ID of the child run
  - `child_run_id`: run ID of the child run
