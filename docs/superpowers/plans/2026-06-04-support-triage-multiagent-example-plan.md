# Support Triage Multi-Agent Example — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `examples/support_triage/` — a multi-agent delegation example where a markdown `triage` orchestrator routes a support ticket to one of three code-built specialist subagents (billing/technical/refund) — plus a shared offline CI test.

**Architecture:** Agents are defined once in `_experts.py` (3 code `ModiAgent`s + tools + a `build_triage_agent()` factory that loads the markdown orchestrator and attaches the experts as subagents). The live `run.py` binds them to a real model; the offline test binds the same factory output to a scripted model via `make_session`. Delegation flows through the `delegate_to_<name>` tool; introspection reads it back from the trace.

**Tech Stack:** Python 3.12, modi-harness V0.5 (`ModiAgent`/`ModiHarness`/`ModiSession`/`ToolBinding`), pytest, rich.

**Spec:** [`docs/superpowers/specs/2026-06-04-support-triage-multiagent-example-design.md`](../specs/2026-06-04-support-triage-multiagent-example-design.md).

**Verified facts (already grepped against the codebase):**
- `ModiAgent.from_markdown(path, *, tools=None, skills=None, subagents=None)` accepts `subagents=`.
- Subagent delegation goes through the tool gateway (`ToolSpec.kind == "subagent"` → dispatcher), so a `delegate_to_<name>` call is recorded like any tool.
- Trace emits **`tool_result`** events (NOT `tool_call`) with `payload["tool_name"]` and `payload["decision"]`. (`graph/nodes.py:345-354`)
- State fallback: `session.get_state(thread_id)["tool_calls"]` is a list of `ToolCallRecord` with `.tool_name` / `.arguments` / `.result`. (`types.py:185`)
- `make_session(tmp_path, *, chat_model, agents=[...])` exists in `_test_fixtures`.
- The `_Script` agent-routing fake model pattern lives in `tests/subagent/test_e2e.py` (sniffs `AGENT_NAME=` in the prompt). Agent instructions must contain a sniffable marker for offline tests — but the example agents should read naturally for the LIVE run, so the test supplies its own routing via the scripted model keyed on agent name. The `_Script` in `test_e2e.py` sniffs `AGENT_NAME=<name>` text in the prompt; our example instructions won't contain that. **Therefore the offline test uses a different fake-model approach: a model that routes on the system/agent instruction text we DO control, or simpler — a model keyed by call order per agent.** See Task 5 for the concrete scripted model.

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `examples/support_triage/agents/triage.md` | Orchestrator markdown: prompt + `tools: delegate_to_*` + `permission_profile.allowed_subagents` |
| `examples/support_triage/_experts.py` | 3 expert `ModiAgent`s + 2 tools (in-memory fake data) + tool specs + `build_triage_agent()` factory |
| `examples/support_triage/run.py` | Live entry: real model, assemble session, run one ticket, print delegation chain |
| `examples/support_triage/README.md` | What it demonstrates + live/offline run instructions |
| `tests/examples/__init__.py` | New test package (empty) |
| `tests/examples/test_support_triage.py` | Offline: scripted model + shared agent tree; 3 tests |

### Modified files

| File | Change |
|---|---|
| `examples/README.md` | Add a `support_triage` index entry |

---

## Task 1: Expert tools + fake data (`_experts.py` part 1)

**Files:**
- Create: `examples/support_triage/_experts.py`
- Test: `tests/examples/test_support_triage.py` (new), `tests/examples/__init__.py` (new, empty)

- [ ] **Step 1: Create the test package**

```bash
mkdir -p tests/examples examples/support_triage/agents
touch tests/examples/__init__.py
```

- [ ] **Step 2: Write failing tests for the tools**

Create `tests/examples/test_support_triage.py`:

```python
"""Offline tests for the support_triage multi-agent example."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_EXPERTS_PATH = Path(__file__).resolve().parents[2] / "examples" / "support_triage" / "_experts.py"


def _load_experts():
    """Load the example's _experts.py by file path (examples/ is not a package)."""
    spec = importlib.util.spec_from_file_location("support_triage_experts", _EXPERTS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lookup_account_known() -> None:
    experts = _load_experts()
    result = experts.lookup_account("acct_123")
    assert result["plan"] == "Pro"
    assert result["account_id"] == "acct_123"


def test_lookup_account_unknown() -> None:
    experts = _load_experts()
    result = experts.lookup_account("nope")
    assert "error" in result


def test_lookup_order_known() -> None:
    experts = _load_experts()
    result = experts.lookup_order("ord_555")
    assert result["refundable"] is True
    assert result["amount"] == 290


def test_lookup_order_unknown() -> None:
    experts = _load_experts()
    result = experts.lookup_order("nope")
    assert "error" in result
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/examples/test_support_triage.py -v`
Expected: FAIL — `FileNotFoundError` / module load error (no `_experts.py` yet).

- [ ] **Step 4: Create `examples/support_triage/_experts.py` with tools + specs**

```python
"""Support-triage example: specialist subagents, their tools, and the
orchestrator factory.

This module is the SINGLE source of agent definitions. Both run.py (live, real
model) and tests/examples/test_support_triage.py (offline, scripted model)
import build_triage_agent() from here — one declaration, two runtimes.
"""

from __future__ import annotations

from pathlib import Path

from modi_harness import ModiAgent, ToolBinding

# ---------------------------------------------------------------------------
# Fake data (in-memory; no external services)
# ---------------------------------------------------------------------------

_ACCOUNTS = {
    "acct_123": {"plan": "Pro", "monthly": 29, "last_charge": "2026-05-01", "status": "active"},
    "acct_999": {"plan": "Free", "monthly": 0, "last_charge": None, "status": "active"},
}

_ORDERS = {
    "ord_555": {"item": "Pro annual", "amount": 290, "purchased": "2026-04-15", "refundable": True},
    "ord_777": {"item": "add-on pack", "amount": 49, "purchased": "2026-01-02", "refundable": False},
}


def lookup_account(account_id: str) -> dict:
    """Return account details for a known id, or an error dict."""
    rec = _ACCOUNTS.get(account_id)
    if rec is None:
        return {"error": f"unknown account {account_id!r}"}
    return {**rec, "account_id": account_id}


def lookup_order(order_id: str) -> dict:
    """Return order details for a known id, or an error dict."""
    rec = _ORDERS.get(order_id)
    if rec is None:
        return {"error": f"unknown order {order_id!r}"}
    return {**rec, "order_id": order_id}


# ---------------------------------------------------------------------------
# Tool specs
# ---------------------------------------------------------------------------

LOOKUP_ACCOUNT_SPEC = {
    "name": "lookup_account",
    "description": "Look up a customer account by id (e.g. acct_123). Returns plan, monthly price, last charge.",
    "input_schema": {
        "type": "object",
        "properties": {"account_id": {"type": "string"}},
        "required": ["account_id"],
        "additionalProperties": False,
    },
    "risk_level": "L0",
    "side_effect": False,
}

LOOKUP_ORDER_SPEC = {
    "name": "lookup_order",
    "description": "Look up an order by id (e.g. ord_555). Returns item, amount, and whether it is refundable.",
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": False,
    },
    "risk_level": "L0",
    "side_effect": False,
}
```

- [ ] **Step 5: Run the tool tests to verify they pass**

Run: `uv run pytest tests/examples/test_support_triage.py -v`
Expected: PASS (4 tool tests).

- [ ] **Step 6: Commit**

```bash
git add examples/support_triage/_experts.py tests/examples/__init__.py tests/examples/test_support_triage.py
git commit -m "feat(example): support_triage tools + fake data (task 1)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Specialist agents + orchestrator markdown + factory (`_experts.py` part 2)

**Files:**
- Modify: `examples/support_triage/_experts.py` (append agents + factory)
- Create: `examples/support_triage/agents/triage.md`
- Test: `tests/examples/test_support_triage.py` (append)

- [ ] **Step 1: Write the failing factory/topology test**

Append to `tests/examples/test_support_triage.py`:

```python
def test_build_triage_agent_topology() -> None:
    experts = _load_experts()
    triage = experts.build_triage_agent()
    assert triage.name == "triage"
    # three specialists attached as subagents
    sub_names = sorted(a.name for a in triage.subagents)
    assert sub_names == ["billing", "refund", "technical"]
    # orchestrator declares delegate tools in its profile
    assert "billing" in (triage.permission_profile or {}).get("allowed_subagents", [])


def test_specialists_have_expected_tools() -> None:
    experts = _load_experts()
    triage = experts.build_triage_agent()
    by_name = {a.name: a for a in triage.subagents}
    assert [t.spec["name"] for t in by_name["billing"].tools] == ["lookup_account"]
    assert [t.spec["name"] for t in by_name["refund"].tools] == ["lookup_order"]
    assert by_name["technical"].tools == ()  # pure-reasoning specialist
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/examples/test_support_triage.py::test_build_triage_agent_topology -v`
Expected: FAIL — `AttributeError: module has no attribute 'build_triage_agent'`.

- [ ] **Step 3: Create `examples/support_triage/agents/triage.md`**

```markdown
---
name: triage
description: Front-line support agent that classifies a ticket and routes it to a specialist.
tools:
  - delegate_to_billing
  - delegate_to_technical
  - delegate_to_refund
permission_profile:
  mode: auto
  allowed_subagents:
    - billing
    - technical
    - refund
---
You are a front-line support triage agent. The user gives you ONE support ticket.

1. Read the ticket and classify it into exactly one category:
   - **billing** — charges, invoices, subscription/payment questions
   - **technical** — errors, bugs, things not working, how-to
   - **refund** — refund requests, cancellations, money-back
2. Delegate the ticket to the matching specialist using the `delegate_to_<category>`
   tool. Pass the original ticket text as `task.ticket` and a one-line `rationale`.
   Delegate to EXACTLY ONE specialist — pick the best fit.
3. When the specialist returns, write a short, friendly final reply to the
   customer that incorporates the specialist's resolution. Do not mention
   internal agents or delegation.
```

- [ ] **Step 4: Append the agents + factory to `_experts.py`**

Append to `examples/support_triage/_experts.py`:

```python
# ---------------------------------------------------------------------------
# Specialist subagents (code-constructed — equivalent to markdown agents)
# ---------------------------------------------------------------------------

billing = ModiAgent(
    name="billing",
    description="Answers billing, charge, and subscription questions.",
    instruction=(
        "You handle billing questions. If the ticket references an account id "
        "(like acct_123), call lookup_account to get the plan and last charge, "
        "then explain the charge clearly. Return a concise resolution."
    ),
    tools=[ToolBinding(spec=LOOKUP_ACCOUNT_SPEC, handler=lookup_account)],
)

technical = ModiAgent(
    name="technical",
    description="Troubleshoots errors and how-to questions.",
    instruction=(
        "You handle technical problems. Give 2-3 concrete troubleshooting steps "
        "for the reported issue. No tools needed; reason from the ticket."
    ),
)

refund = ModiAgent(
    name="refund",
    description="Processes refund and cancellation requests.",
    instruction=(
        "You handle refunds. If the ticket references an order id (like ord_555), "
        "call lookup_order. If refundable, approve and state the amount; if not, "
        "explain why and offer an alternative. Return a concise resolution."
    ),
    tools=[ToolBinding(spec=LOOKUP_ORDER_SPEC, handler=lookup_order)],
)


def build_triage_agent() -> ModiAgent:
    """Load the markdown orchestrator and attach the 3 code-built experts.

    Shared by run.py (live) and the offline test — one declaration, two runtimes.
    """
    here = Path(__file__).parent
    return ModiAgent.from_markdown(
        here / "agents" / "triage.md",
        subagents=[billing, technical, refund],
    )
```

- [ ] **Step 5: Run the topology tests to verify they pass**

Run: `uv run pytest tests/examples/test_support_triage.py -v`
Expected: PASS (4 tool tests + 2 topology tests = 6).

- [ ] **Step 6: Commit**

```bash
git add examples/support_triage/_experts.py examples/support_triage/agents/triage.md tests/examples/test_support_triage.py
git commit -m "feat(example): support_triage specialists + orchestrator + factory (task 2)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Offline delegation behavior tests (scripted model)

**Files:**
- Test: `tests/examples/test_support_triage.py` (append the 3 spec tests)

This task adds the spec's three behavior tests using a scripted chat model that
routes generations by the running agent's name. We need a fake model that can
serve a different script per agent. The agent's identity is available because
each agent's instruction is distinct — but matching on instruction text is
brittle. Instead, use a call-order script PER AGENT keyed by detecting which
agent's instruction is in the prompt via a stable substring we control in the
*example* instructions ("triage agent", "billing questions", "refunds",
"technical problems").

- [ ] **Step 1: Write the scripted model + delegation test**

Append to `tests/examples/test_support_triage.py`:

```python
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiHarness, ModiSession
from modi_harness.api.errors import AgentNotRegistered


class _RoutingScript(BaseChatModel):
    """Serves a per-agent script. Picks the script by matching a stable
    substring of each agent's instruction in the prompt."""

    by_marker: dict[str, list[Any]] = Field(default_factory=dict)
    cursor: dict[str, int] = Field(default_factory=dict)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        marker = self._match(messages)
        i = self.cursor.get(marker, 0)
        seq = self.by_marker.get(marker, [])
        if i >= len(seq):
            raise RuntimeError(f"_RoutingScript for {marker!r} exhausted at {i}")
        self.cursor[marker] = i + 1
        return ChatResult(generations=[ChatGeneration(message=seq[i])])

    def _match(self, messages) -> str:
        text = " ".join(
            (getattr(m, "content", "") or "") for m in messages
            if isinstance(getattr(m, "content", ""), str)
        )
        # markers are stable phrases from each agent's instruction
        if "triage agent" in text:
            return "triage"
        if "billing questions" in text:
            return "billing"
        if "refunds" in text:
            return "refund"
        if "technical problems" in text:
            return "technical"
        return "unknown"

    @property
    def _llm_type(self) -> str:
        return "routing_script"


def _session(tmp_path, script) -> ModiSession:
    experts = _load_experts()
    triage = experts.build_triage_agent()
    harness = ModiHarness(chat_model=script)
    return ModiSession(
        harness=harness,
        agents=[triage],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        max_steps=20,
    )


def _refund_script() -> _RoutingScript:
    return _RoutingScript(by_marker={
        "triage": [
            AIMessage(content="", tool_calls=[{
                "name": "delegate_to_refund",
                "args": {"task": {"ticket": "refund ord_555"}, "rationale": "refund request"},
                "id": "tc-del",
            }]),
            AIMessage(content="Your refund of $290 for ord_555 is approved."),
        ],
        "refund": [
            AIMessage(content="", tool_calls=[{
                "name": "lookup_order", "args": {"order_id": "ord_555"}, "id": "tc-lo",
            }]),
            AIMessage(content="Order ord_555 is refundable for $290. Approved."),
        ],
    })


def test_triage_routes_to_refund(tmp_path) -> None:
    s = _session(tmp_path, _refund_script())
    resp = s.run_task(
        agent="triage",
        input={"goal": "Resolve the support ticket.",
               "messages": [{"role": "user", "content": "Please refund order ord_555."}]},
        thread_id="t-refund",
    )
    assert resp["status"] == "completed"
    out = str(resp["output"])
    assert "290" in out or "refund" in out.lower()
```

> **Note for implementer:** the markers (`triage agent`, `billing questions`,
> `refunds`, `technical problems`) must literally appear in the corresponding
> agent instructions in `_experts.py` / `triage.md`. Verify after writing:
> - `triage.md` body contains "triage agent" ✓ (it says "front-line support triage agent")
> - billing instruction contains "billing questions" ✓
> - refund instruction contains "refunds" ✓ (it says "You handle refunds")
> - technical instruction contains "technical problems" ✓
> If any marker is missing, adjust the instruction text (keeping it natural) OR
> the marker string so they match. Do this BEFORE running — a mismatch makes the
> fake model return the wrong script.

- [ ] **Step 2: Run to verify it passes**

Run: `uv run pytest tests/examples/test_support_triage.py::test_triage_routes_to_refund -v`
Expected: PASS. If it fails with "exhausted" or wrong-script, a marker didn't match — fix the marker/instruction and re-run.

- [ ] **Step 3: Add the isolation test**

Append:

```python
def test_specialist_isolation(tmp_path) -> None:
    s = _session(tmp_path, _RoutingScript(by_marker={}))
    assert s.list_agents() == ["triage"]
    assert set(s.list_all_agents()) == {"triage", "billing", "technical", "refund"}
    with pytest.raises(AgentNotRegistered):
        s.run_task(agent="refund", input={"goal": "x", "messages": []})
```

- [ ] **Step 4: Add the trace-visibility test**

Append:

```python
def test_delegation_appears_in_trace(tmp_path) -> None:
    s = _session(tmp_path, _refund_script())
    resp = s.run_task(
        agent="triage",
        input={"goal": "Resolve the support ticket.",
               "messages": [{"role": "user", "content": "Please refund order ord_555."}]},
        thread_id="t-trace",
    )
    tid = resp["thread_id"]
    tool_names = [
        ev["payload"].get("tool_name")
        for ev in s.get_trace(tid)
        if ev["event_type"] == "tool_result"
    ]
    assert "delegate_to_refund" in tool_names
    assert "lookup_order" in tool_names
```

- [ ] **Step 5: Run all example tests**

Run: `uv run pytest tests/examples/test_support_triage.py -v`
Expected: PASS — 4 tool + 2 topology + 3 behavior = 9 tests.

- [ ] **Step 6: Run the full suite to confirm the new package integrates**

Run: `uv run pytest -q`
Expected: 524 + 9 = 533 passed (or current-baseline + 9).

- [ ] **Step 7: Commit**

```bash
git add tests/examples/test_support_triage.py
git commit -m "test(example): support_triage offline delegation/isolation/trace tests (task 3)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Live `run.py` + delegation-chain printing

**Files:**
- Create: `examples/support_triage/run.py`

No unit test (it needs a real model); verified by `py_compile` + a construction
smoke check with a fake model.

- [ ] **Step 1: Create `examples/support_triage/run.py`**

```python
"""Modi Harness — Support Triage (multi-agent delegation).

A markdown `triage` orchestrator classifies a support ticket and routes it to
one of three code-built specialist subagents (billing / technical / refund),
then summarizes the reply. After the run, the delegation chain is printed from
the session trace.

Demonstrates V0.5 capabilities the other examples don't: recursive subagents,
delegate_to_<name> + allowed_subagents governance, agent isolation, markdown vs
code agents, and introspection.

Run from the repo root (needs a model API key in .env):
    uv run python examples/support_triage/run.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from rich.console import Console

from modi_harness import ModiHarness, ModiSession
from modi_harness.config import Settings
from modi_harness.models import create_chat_model

sys.path.insert(0, str(Path(__file__).parent))
from _experts import build_triage_agent  # noqa: E402  (local example module)

# Three sample tickets; change DEFAULT_TICKET to exercise a different route.
TICKETS = {
    "billing": "I was charged $29 on account acct_123 but I thought I cancelled. Why?",
    "refund": "Please refund order ord_555 — I changed my mind within the window.",
    "technical": "The export button does nothing when I click it. How do I fix this?",
}
DEFAULT_TICKET = TICKETS["billing"]


def print_delegation_chain(console: Console, session: ModiSession, thread_id: str) -> None:
    """Surface the delegation + specialist tool calls from the trace.

    Trace emits `tool_result` events carrying payload['tool_name']
    (graph/nodes.py). We pull the delegate_to_* call and any specialist tool
    calls to show the route the ticket took.
    """
    console.print("\n[bold]Delegation chain:[/bold]")
    saw_any = False
    for ev in session.get_trace(thread_id):
        if ev["event_type"] != "tool_result":
            continue
        name = ev["payload"].get("tool_name", "")
        if name.startswith("delegate_to_"):
            console.print(f"  triage ──delegate──▶ {name.removeprefix('delegate_to_')}")
            saw_any = True
        elif name in ("lookup_account", "lookup_order"):
            console.print(f"           specialist called {name}")
            saw_any = True
    if not saw_any:
        console.print("  [dim](no delegation recorded)[/dim]")


def main() -> int:
    console = Console()
    console.print()
    console.print("[bold cyan]Modi Harness — Support Triage[/bold cyan]")
    console.print("[dim]Multi-agent delegation: triage → billing/technical/refund[/dim]")
    console.print()

    settings = Settings()
    if not settings.model.api_key:
        console.print("[red]Error:[/red] MODI_MODEL_API_KEY not set in .env")
        console.print("[dim]Copy .env.example to .env and fill in your API key.[/dim]")
        return 1

    chat_model = create_chat_model(
        provider=settings.model.provider,
        name=settings.model.name,
        api_key=settings.model.api_key,
        base_url=settings.model.base_url,
    )

    triage = build_triage_agent()
    harness = ModiHarness(chat_model=chat_model)
    session = ModiSession(
        harness=harness,
        agents=[triage],
        checkpointer=MemorySaver(),
        workspace_root=".modi/workspace",
        memory_root="~/.modi/memory",
        max_steps=20,
    )

    console.print(f"[dim]top-level (runnable):[/dim] {session.list_agents()}")
    console.print(f"[dim]all agents (incl. nested):[/dim] {session.list_all_agents()}")
    console.print(f"\n[bold]Ticket:[/bold] {DEFAULT_TICKET}\n")

    response = session.run_task(
        agent="triage",
        input={"goal": "Resolve the support ticket.",
               "messages": [{"role": "user", "content": DEFAULT_TICKET}]},
        mode="auto",
    )

    console.print("[bold]Final reply:[/bold]")
    console.print(response.get("output"))
    print_delegation_chain(console, session, response["thread_id"])
    return 0 if response["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Byte-compile**

Run: `uv run python -m py_compile examples/support_triage/run.py`
Expected: no output (compiles clean).

- [ ] **Step 3: Construction smoke check (offline, fake model)**

Run:
```bash
cd /Users/frank/Desktop/codes/modi-harness && uv run python -c "
import sys; from pathlib import Path
sys.path.insert(0, 'examples/support_triage')
from _experts import build_triage_agent
from modi_harness import ModiHarness, ModiSession
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.language_models.fake_chat_models import FakeListChatModel
t = build_triage_agent()
s = ModiSession(harness=ModiHarness(chat_model=FakeListChatModel(responses=['ok'])),
                agents=[t], checkpointer=MemorySaver(),
                workspace_root='/tmp/st_ws', memory_root='/tmp/st_mem')
assert s.list_agents() == ['triage']
assert set(s.list_all_agents()) == {'triage','billing','technical','refund'}
print('construction OK')
"
```
Expected: `construction OK`.

- [ ] **Step 4: Ruff**

Run: `uv run ruff check examples/support_triage/run.py examples/support_triage/_experts.py`
Expected: clean (the `sys.path.insert` + `noqa: E402` on the local import is intentional; if ruff still complains, the noqa code may differ — adjust to the exact code ruff reports).

- [ ] **Step 5: Commit**

```bash
git add examples/support_triage/run.py
git commit -m "feat(example): support_triage live run.py + delegation-chain printing (task 4)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: README + index entry

**Files:**
- Create: `examples/support_triage/README.md`
- Modify: `examples/README.md`

- [ ] **Step 1: Create `examples/support_triage/README.md`**

```markdown
# Support Triage — Multi-Agent Delegation

A markdown `triage` orchestrator classifies a support ticket and routes it to
one of three code-built specialist subagents — `billing`, `technical`, or
`refund` — then summarizes the reply.

Demonstrates V0.5 capabilities the other examples don't:

- **Recursive subagents** — `ModiAgent(..., subagents=[...])`
- **`delegate_to_<name>` + `allowed_subagents`** governance
- **Markdown vs code agents, equivalent** — the orchestrator is markdown
  (`agents/triage.md`); the experts are `ModiAgent(...)` in `_experts.py`
- **Agent isolation** — specialists are not top-level runnable
- **Introspection** — the delegation chain is printed from `session.get_trace(...)`
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
| `run.py` | Live entry: assemble session, run a ticket, print the delegation chain |
```

- [ ] **Step 2: Add an index entry to `examples/README.md`**

Read `examples/README.md` first to match its format. Add a row/section for
`support_triage` alongside the existing three examples, with a one-line
description: "Multi-agent delegation — a triage orchestrator routes tickets to
specialist subagents." Place it consistently with how the other examples are
listed (table row or bullet — match the existing style exactly).

- [ ] **Step 3: Ruff (markdown is not linted, but confirm nothing else broke)**

Run: `uv run pytest tests/examples/ -q`
Expected: 9 passed (unchanged — docs don't affect tests).

- [ ] **Step 4: Commit**

```bash
git add examples/support_triage/README.md examples/README.md
git commit -m "docs(example): support_triage README + index entry (task 5)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Acceptance Gate

Run these in order; all must pass.

- [ ] `uv run pytest tests/examples/test_support_triage.py -v` — 9 passed.
- [ ] `uv run pytest -q` — full suite green (baseline + 9).
- [ ] `uv run python -m py_compile examples/support_triage/run.py` — clean.
- [ ] Construction smoke check from Task 4 Step 3 — prints `construction OK`.
- [ ] `uv run ruff check examples/support_triage/ tests/examples/` — clean.
- [ ] `examples/README.md` has a `support_triage` entry.
- [ ] `_experts.py` is imported by BOTH `run.py` and the test (grep
      `build_triage_agent` — appears in run.py and test_support_triage.py).
