# Code Auditor — Modi Harness Example

A real, end-to-end demo that uses Modi Harness to audit its own source tree.

## What it does

The `code-auditor` agent:
1. Lists every Python file under `src/modi_harness/` with line counts
2. Picks the 5 largest files
3. Reads each one
4. Produces a Markdown report with a quality score and improvement suggestion per file

All output streams live to your terminal, token by token.

## What it shows off

| Capability | Where you see it |
|---|---|
| Multi-provider Model Adapter | Picks up provider/key from `.env` |
| Async streaming via `astream` | Tokens appear as Claude generates them |
| Live tool call rendering | `▸ list_python_files(...)` markers in cyan |
| Tool gateway | Two custom tools registered at runtime |
| Rich CLI experience | Colored output, panels, status line |
| Workspace / trace recording | Full trace under `.modi/workspace/<run_id>/` |

## Run it

From the repo root:

```bash
uv run python examples/code_auditor/run.py
```

You need a populated `.env` with at least:

```bash
MODI_MODEL_PROVIDER=anthropic
MODI_MODEL_NAME=claude-sonnet-4-20250514
MODI_MODEL_API_KEY=sk-ant-...
```

(Or `openai` with `gpt-4o`, etc. The factory handles both.)

## Files

- `agents/code-auditor.md` — agent definition (instruction, tools, safety constraints)
- `run.py` — script: registers two tools, constructs the harness, calls `run_streaming`

## Try modifying it

- Change the user prompt in `run.py` to ask for a security audit instead
- Add a `grep_codebase` tool to let the agent search for patterns
- Switch the agent to a different model via the per-agent `model:` block in `code-auditor.md`
- Drop `permission_mode="bypass"` to see approvals fire on tool calls
