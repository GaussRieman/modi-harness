# Modi CLI Guide

The `modi` command is the user-facing entry point to Modi Harness. Internally it
performs the V0.5 two-stage build — `ModiHarness(...)` (capability suite) →
`ModiAgent.load_dir(...)` → `ModiSession(...)` — and then drives the session's
`run_task` / `astream` / `resume_task` API, adapting its output to whichever
endpoint stdout is attached to: a TTY gets a live, colored stream; a pipe gets a
single JSON document. The command surface below is unchanged for users.

> **Status:** new in V0.4b. Requires `rich>=13.7` (installed automatically with
> `modi-harness`).

## Installation check

```bash
modi --version           # prints the package version
modi info                # prints version + config diagnostics
```

## Basic usage

Run a task against a configured agent:

```bash
modi run --agent support-bot --task task.json
```

Where `task.json` is the input payload accepted by `ModiSession.run_task`. The
harness derives the agent's first user message from recognized keys —
`messages`, `prompt`, `customer_message`, `question`, or `goal` (see
[Harness API](architecture/08-harness-api.md) for precedence). A minimal payload
is `{"prompt": "..."}` or `{"messages": [{"role": "user", "content": "..."}]}`.
Pipe it from stdin with `--task -`:

```bash
echo '{"prompt": "hello"}' | modi run --agent support-bot --task -
```

Optional flags:

| Flag | Default | Purpose |
|------|---------|---------|
| `--agents-dir PATH` | (required) | Directory of agent `.md` files to load |
| `--thread-id ID` | new thread | Resume or attach to a specific thread |
| `--permission-mode MODE` | (agent default) | Override mode: `ask` / `auto` / `plan` / `bypass` |
| `--stream` | auto | Force live streaming output |
| `--no-stream` | auto | Force single-shot JSON output |

## What live streaming looks like

When stdout is a TTY, the CLI invokes `session.astream(...)` and renders each
event through `StreamRenderer`:

- `model_delta` events print token-by-token, inline, with no markup
  interpretation (so model text cannot inject ANSI styles).
- `tool_call_proposal` events render a compact cyan marker:
  `▸ tool_name({"arg": "value"})` (arguments truncated to 80 chars).
- `tool_call_result` events render a cyan return marker:
  `← <truncated result>` (truncated to 200 chars).
- `approval_request` events open an inline panel and prompt for a decision
  (see below).
- `terminal` events close the run with a colored status line:
  - green `✓ completed in 1.4s`
  - red `✗ failed` / `✗ blocked`
  - yellow `⏸ interrupted`

Example terminal session:

```text
[support-bot] running...
Hello, I'd like to help.
▸ search_docs({"query": "refund policy"})
← Refunds are accepted within 30 days...
The refund window is 30 days.
✓ completed in 2.1s
```

## Approval prompts

When the agent proposes a tool call that requires human review, the CLI pauses
the stream and shows an approval panel summarizing the tool, arguments, risk
level, and `decision_kind`. You answer with a single keypress:

| Keystroke | Effect |
|-----------|--------|
| `a` | Approve. The CLI calls `session.approve_action(thread_id, approval_id)` and continues. |
| `r` | Reject. The CLI prompts for a one-line reason; it calls `session.reject_action(thread_id, approval_id, reason)`. |
| `d` | Show full details — full args, tail of recent messages, denied-action count, agent safety constraints — then re-prompt. |

The `d` (details) path loops until you choose `a` or `r`.

## TTY auto-detection

By default the CLI inspects `sys.stdout.isatty()`:

- **TTY → streaming.** Live colored output, inline approval prompts.
- **Pipe → JSON.** A single `RunTaskResponse` is dumped via `json.dumps`, ready
  for `jq`, scripts, or CI logs.

Override with the explicit flags:

```bash
modi run --agent X --task t.json --no-stream         # JSON even in a TTY
modi run --agent X --task t.json --stream | tee log  # streaming into a tee
```

CI runners are non-TTY by default, so existing pipelines see the same JSON
shape they always have without changes.

## Resuming after an interrupt

Threads persist via the configured checkpointer. If a run is interrupted (for
example, the process exits between an approval request and its response), pick
it back up with `modi resume`:

```bash
modi resume --thread-id 01J6V... --payload payload.json
```

`--payload` defaults to stdin (`-`); the file or stream contents are passed
verbatim to `session.resume_task(thread_id, payload)`. A typical resume payload
for an approval is `{"decision": "approved"}` or
`{"decision": "rejected", "reason": "out of scope"}`.

## Environment variables

The CLI honors the same environment as the harness build. Common keys:

- `MODI_MODEL_PROVIDER`, `MODI_MODEL_NAME`, `MODI_MODEL_API_KEY`,
  `MODI_MODEL_BASE_URL` — default model selection.
- `MODI_PERMISSION_MODE` — default permission mode.
- `MODI_CHECKPOINT_BACKEND` — `memory` / `sqlite` (default) / `postgres`.
- `MODI_CHECKPOINT_SQLITE_PATH`, `MODI_CHECKPOINT_POSTGRES_DSN`.

For the full list and defaults, see
[`docs/implement/00-project-foundation.md`](implement/00-project-foundation.md).

## Related docs

- [Harness API](architecture/08-harness-api.md) — the `astream` / `run_task`
  contracts the CLI consumes.
- [Permission Mode](architecture/14-permission-mode.md) — what `ask` / `auto` /
  `plan` / `bypass` actually mean.
- [Development Plan](superpowers/plans/development-plan.md) — release history,
  including V0.4b CLI experience milestones.
