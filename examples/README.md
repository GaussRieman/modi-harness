# Modi Harness Examples

Runnable end-to-end demos that use the live harness against real models.

| Example | What it shows |
|---|---|
| [`code_auditor/`](code_auditor/) | Audits the modi-harness source tree itself. Streaming, tools, structured output. |

Each example is self-contained:

```bash
uv run python examples/<name>/run.py
```

All examples read provider/key from `.env` (see `.env.example`).
