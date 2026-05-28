# Hook System

## Module

`modi_harness.hooks`

## Purpose

Load hook configuration, dispatch events, capture hook results, and feed them back into the runtime loop.

## Configuration Files

```text
<user_root>/.modi/settings.json
<project_root>/.modi/settings.json
```

Schema fragment:

```json
{
  "hooks": [
    {
      "event": "pre_tool_use",
      "matcher": {"tool": "shell", "risk_level": "L3"},
      "command": "python:my_project.audit.require_ticket",
      "timeout_seconds": 5,
      "blocking": true,
      "pass_payload": "stdin",
      "capture": "stdout",
      "on_failure": "block"
    }
  ]
}
```

Merge rule: user-level hooks first, project-level hooks last; project overrides when `event + matcher` are identical.

## Design

Implement:

- `HookRegistry` — loads, validates, indexes hooks by event.
- `HookDispatcher` — receives `(event, payload, context)`, returns `list[HookResult]`.
- `HookRunner` — executes a single hook with timeout, payload marshalling, and output capture.
- `PythonHookLoader` — resolves `python:module.function` targets via importlib.
- `ShellHookRunner` — executes shell commands using `subprocess.run` with strict timeout and env isolation.

No LangChain or LangGraph dependency.

## Payload Marshalling

- `env`: payload flattened to `MODI_HOOK_*` environment variables.
- `stdin`: payload serialized to JSON and piped to stdin.
- `argv`: payload's top-level scalar fields appended as `--key=value` args; complex fields fall back to stdin JSON.

Returned JSON on stdout, when present, is parsed into `HookResult.decision`, `feedback`, and `redirect`. Non-JSON stdout becomes `feedback` text and decision defaults to `proceed`.

## Event Hookup

Runtime Adapter calls `HookDispatcher.dispatch(event, payload)` at each event point. The dispatcher:

1. Selects hooks matching event and matcher.
2. Runs them in registration order. `blocking=true` hooks run synchronously; non-blocking hooks run synchronously but their results are advisory.
3. Returns aggregated results. The first `block` short-circuits subsequent blocking hooks at the same event.

## Matcher

Common keys:

- `tool`: tool name
- `risk_level`: `L0..L4`
- `agent`: agent name
- `skill`: skill name
- `mode`: permission mode
- `tag`: any tag on the action

Matchers are AND-combined; absent keys are wildcards.

## Trace

Every dispatch writes a `hook_dispatch` trace event with the list of `HookResult`. Large stdout/stderr is saved as workspace references and only the ref is in trace.

## Rules

- Hook commands run with `cwd = <project_root>` and a scrubbed env (`MODI_*` allowed, plus user-listed `pass_env`).
- Timeout default 10 seconds, hard kill on overrun.
- Shell hooks never see model API keys unless explicitly passed via `pass_env`.
- Python hooks import lazily on first dispatch; import failure is recorded once and the hook is marked disabled for the run.
- `on_failure: block` raises a runtime interrupt with reason `hook_block`.

## Settings

Add to `Settings`:

```text
MODI_HOOK_USER_SETTINGS=~/.modi/settings.json
MODI_HOOK_PROJECT_SETTINGS=.modi/settings.json
MODI_HOOK_TIMEOUT_DEFAULT=10
MODI_HOOK_PASS_ENV=PATH,LANG,LC_ALL
```

## Integration

- `RuntimeAdapter` owns event dispatch and converts blocking hook feedback into a state update equivalent to a denied action.
- `ToolGateway` calls `pre_tool_use` and `post_tool_use` through the dispatcher rather than calling hooks directly.
- `HarnessAPI` exposes `list_hooks(run_id=None)` (returns the merged `HookSpec` set effective for the run, or globally when `run_id=None`) and `get_hook_result(run_id, hook_dispatch_id)` (returns the `list[HookResult]` for a recorded dispatch event).
- `TraceRecorder` records hook events with redaction; each dispatch carries a `hook_dispatch_id` referenced by `get_hook_result`.

## Tests

- shell hook proceed/block/redirect
- python hook proceed/block/redirect
- matcher precedence and merge
- timeout kill
- non-JSON stdout becomes feedback
- on_failure semantics
- redirect only honored on allowed events
- hook denial is not retried unchanged
