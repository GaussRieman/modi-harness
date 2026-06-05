# Modi Harness Examples

Runnable end-to-end demos that use the live harness against real models.

| Example | What it shows |
|---|---|
| [`research_assistant_simple/`](research_assistant_simple/) | Single agent, auto-generated output schema. The minimal end-to-end demo. |
| [`research_assistant/`](research_assistant/) | Single agent with skills and a cited-briefing output contract. |
| [`code_auditor/`](code_auditor/) | Audits the modi-harness source tree itself. Streaming, tools, structured output. |
| [`support_triage/`](support_triage/) | Multi-agent delegation — a triage orchestrator routes tickets to specialist subagents. Markdown + code agents, introspection. |

Each example is self-contained:

```bash
uv run python examples/<name>/run.py
```

All examples read provider/key from `.env` (see `.env.example`).
