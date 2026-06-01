# Research Assistant — Modi Harness Example

A research-assistant agent that takes a question + a small set of source URLs and produces a structured, cited briefing — exercising most of modi-harness's runtime surface in one run.

## What it shows off

| Capability | Where you see it |
|---|---|
| Custom domain tool | `fetch_url` (stdlib `urllib`, no extra deps) |
| **V0.4d builtin tools** | `save_draft`, `save_artifact`, `recall_memory`, `save_memory` — implicit, never listed in `agent.md` |
| **Skills** | `source-evaluation` and `briefing-structure` loaded from `./skills/` |
| **Output contract** | Structured briefing JSON validated for `required_fields`, `citation_required`, `risk_label_required` |
| **Memory** | Agent calls `recall_memory` at the start, `save_memory` at the end — preferences persist across runs |
| **Untrusted-block wrapping** | Web content auto-wrapped as `<untrusted>` by the Context Manager |
| **Trace** | Full event log under `<run>/logs/trace.jsonl` |
| **Workspace** | Briefing JSON in `<run>/drafts/`, Markdown rendering in `<run>/artifacts/` |
| **Streaming** | Live token-by-token output via `astream` + `rich` |
| **State** | LangGraph checkpointer persists state per `thread_id` |

## Run it

From the repo root:

```bash
# Default: research transformers vs RNNs from three Wikipedia pages
uv run python examples/research_assistant/run.py

# Or pass your own URLs
uv run python examples/research_assistant/run.py https://example.com/a https://example.com/b
```

You need `.env` with at least:

```bash
MODI_MODEL_PROVIDER=anthropic
MODI_MODEL_NAME=claude-sonnet-4-20250514
MODI_MODEL_API_KEY=sk-ant-...
```

## What gets produced

After a successful run:

```
.modi/workspace/<run_id>/
├── drafts/briefing.json       # structured briefing (the validated output)
├── artifacts/briefing.md      # human-readable rendering
└── logs/trace.jsonl           # every model call, tool call, decision
```

Plus an agent-scope memory record under `~/.modi/memory/agent/` if the agent decided a preference worth persisting surfaced this run.

## Files

- `agents/research-assistant.md` — agent definition: tools, skills, output contract, safety constraints, `permission_profile.mode: auto`
- `skills/source-evaluation/SKILL.md` — grade each fetched source
- `skills/briefing-structure/SKILL.md` — assemble the final JSON
- `run.py` — registers `fetch_url`, constructs the harness, calls `run_streaming`

## Compare with `code_auditor`

| Feature | `code_auditor` | `research_assistant` |
|---|---|---|
| Custom tools | 2 (`list_python_files`, `read_file`) | 1 (`fetch_url`) |
| Builtins used | none | 4 (`save_draft`, `save_artifact`, `recall_memory`, `save_memory`) |
| Skills | none | 2 |
| Output contract | free-form Markdown | structured JSON, validated |
| Memory | none | recall + save |
| Permission mode | `bypass` | `auto` |

`code_auditor` is the minimum viable example for "register a domain tool and run." `research_assistant` is the demo for "everything modi-harness can do, in one run."

## Try modifying it

- Drop `fetch_url` and add a `pdf_extract` tool — same pattern, different domain.
- Set `permission_mode="ask"` in `run.py` and watch L1 tools (including `save_artifact`) prompt for approval.
- Inspect `~/.modi/memory/agent/*.md` after a run to see what preferences the agent decided to persist.
- Run twice with different questions and watch the second run's `recall_memory` event in `trace.jsonl` pick up the prior preferences.
