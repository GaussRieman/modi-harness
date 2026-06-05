# Support Triage — Multi-Agent Delegation

A markdown `triage` orchestrator classifies a support ticket and routes it to
one of three code-built specialist subagents — `billing`, `technical`, or
`refund` — then summarizes the reply.

Demonstrates V0.5 capabilities the other examples don't:

- **Recursive subagents** — `ModiAgent(..., subagents=[...])`
- **`delegate_to_<name>` + `allowed_subagents`** governance
- **Markdown vs code agents, equivalent** — the orchestrator is markdown
  (`agents/triage.md`); the experts are `ModiAgent(...)` in `_experts.py`
- **Agent isolation** — specialists are not top-level runnable, and their tool
  calls run in isolated child traces
- **Introspection** — the delegation route is printed from `session.get_trace(...)`
- **One agent declaration, two runtimes** — `_experts.py` is shared by `run.py`
  (live, real model) and the CI test (offline, scripted model)

## Run live (needs a model API key)

```bash
cp .env.example .env   # fill MODI_MODEL_API_KEY
uv run python examples/support_triage/run.py
```

Edit `DEFAULT_TICKET` in `run.py` to route a billing / refund / technical ticket.

## Run offline (no key — this is the CI test)

```bash
uv run pytest tests/examples/test_support_triage.py -v
```

## Files

| File | Role |
|------|------|
| `agents/triage.md` | Orchestrator (markdown): classify + delegate + summarize |
| `_experts.py` | 3 specialist `ModiAgent`s + tools + `build_triage_agent()` factory |
| `run.py` | Live entry: assemble session, run a ticket, print the delegation route |
