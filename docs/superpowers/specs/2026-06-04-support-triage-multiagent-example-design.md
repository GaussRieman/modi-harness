# Support Triage — Multi-Agent Delegation Example (Design)

**Status:** Design approved 2026-06-04. Awaiting implementation plan.
**Type:** New example (`examples/support_triage/`) + CI test.
**Motivation:** The three existing examples (`research_assistant`,
`research_assistant_simple`, `code_auditor`) all exercise only the most basic
V0.5 path — a single `ModiAgent.from_markdown` + `MemorySaver` + streaming run.
None demonstrate the multi-agent delegation that is modi-harness's stated
positioning ("a substrate for the next Claude Code / Codex / coworker"). This
example fills that gap as the first of a planned example suite.

---

## 1. Goal

Show subagent delegation as the canonical "orchestrator routes a task to one
specialist" pattern, using a support-ticket triage scenario. The example
doubles as the reference for several V0.5 capabilities no existing example
covers.

**V0.5 capabilities demonstrated (vs the gap analysis):**

- Recursive subagents: `ModiAgent(..., subagents=[...])`.
- `delegate_to_<name>` tool + `permission_profile.allowed_subagents` governance.
- Multiple agents isolated in one `ModiSession`.
- Markdown- and code-constructed agents, equivalent (orchestrator is markdown;
  experts are `ModiAgent(...)`).
- Agent isolation: specialists are not top-level runnable.
- Introspection: print the delegation chain from `session.get_trace(...)` and
  `session.list_all_agents()`.
- One agent declaration, two runtimes: a shared `_experts.py` is consumed by
  both the live `run.py` (real model) and the offline CI test (scripted model).

**Explicitly out of scope** (deferred to future examples):

- `model_override` / per-agent model selection.
- Streaming render (this example deliberately uses non-streaming `run_task` +
  post-run introspection).
- Fan-out delegation (this example is route-to-one).
- Real external data/services (in-memory fake data only).

## 2. Scenario and Agent Topology

A customer support triage system. The user submits one support ticket (natural
language). A `triage` orchestrator reads it, classifies it into exactly one
category, delegates the ticket to the matching specialist subagent, receives
the specialist's resolution, and writes the final customer-facing reply.

```
                    ┌─────────────┐
   ticket ────────▶ │   triage    │  (orchestrator, markdown)
                    │ read→classify│
                    └──────┬──────┘
                           │ delegate_to_<one>
            ┌──────────────┼──────────────┐
            ▼              ▼               ▼
     ┌───────────┐  ┌───────────┐  ┌───────────┐
     │  billing  │  │ technical │  │  refund   │   (3 experts, code-built)
     │           │  │           │  │           │
     └───────────┘  └───────────┘  └───────────┘
       lookup_account              lookup_order
```

| Agent | Construction | Tools | Responsibility |
|---|---|---|---|
| `triage` | markdown (`agents/triage.md`) | `delegate_to_billing` / `delegate_to_technical` / `delegate_to_refund` | Read ticket, classify, delegate to one specialist, summarize the reply |
| `billing` | code (`_experts.py`) | `lookup_account` | Answer billing / charge / subscription questions |
| `technical` | code | (none — pure reasoning) | Troubleshoot errors / how-to; give concrete steps |
| `refund` | code | `lookup_order` | Process refund / cancellation requests |

**Design points:**

- `triage.permission_profile.allowed_subagents = [billing, technical, refund]`
  explicitly authorizes delegation to exactly those three.
- The three specialists are mutually invisible and non-delegating (they are not
  in each other's `subagents`, and have no delegate tools) — demonstrating
  isolation.
- Tools use in-memory fake data (a few hard-coded accounts/orders); offline and
  online both run with zero external dependencies.
- `technical` deliberately has no tools — demonstrates a pure-reasoning subagent.
- No `output_contract` on any agent: keeps the example focused on delegation
  (structured output is already shown by `research_assistant`).

## 3. File Structure

```
examples/support_triage/
├── README.md            # what it demonstrates + how to run (live / offline)
├── run.py               # live entry: assemble harness×agents×session, real model, print delegation chain
├── agents/
│   └── triage.md        # orchestrator markdown declaration
└── _experts.py          # 3 expert ModiAgents (code) + tools + build_triage_agent() factory

tests/examples/
├── __init__.py
└── test_support_triage.py   # offline: scripted model, assert delegation chain + isolation
```

**Responsibilities (single, independently understandable):**

| File | Responsibility | Depends on |
|---|---|---|
| `_experts.py` | 3 expert `ModiAgent`s + 2 tools (`lookup_account` / `lookup_order`, in-memory fake data) + `build_triage_agent()` factory that loads the markdown orchestrator and attaches the experts as subagents. **The single source of agent definitions.** | `modi_harness` (`ModiAgent`, `ToolBinding`) |
| `agents/triage.md` | Orchestrator prompt + frontmatter (`tools: delegate_to_*`, `permission_profile.allowed_subagents`). | none |
| `run.py` | Read `.env` real model (error+exit if no key); import `_experts` agent tree; build `ModiHarness` + `ModiSession`; run one sample ticket; print final reply; replay delegation chain via introspection. | `_experts`, `modi_harness`, `cli.runner`/introspection |
| `test_support_triage.py` | Import the same `_experts` agent tree, bind a scripted model via `make_session`, assert: delegation happened, routed to the right specialist, specialists isolated, final summary. **Runs in CI.** | `_experts`, `modi_harness._test_fixtures` |
| `README.md` | Top "what it demonstrates" checklist; live/offline run instructions. | none |

**Core invariant (the lesson):** `_experts.py` defines the agents once;
`run.py` (live, real model) and `test_support_triage.py` (offline, fake model)
share the same declaration. This is a working demonstration of "ModiAgent is a
declaration, ModiSession executes" — one declaration bound to different
infra/model.

`_experts.py` uses an underscore prefix to mark it as the example's internal
assembly module (imported by run.py and the test), not a public API surface.

## 4. Agent Definitions and Tools

### `agents/triage.md`

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

### `_experts.py` — fake data + tools

```python
_ACCOUNTS = {
    "acct_123": {"plan": "Pro", "monthly": 29, "last_charge": "2026-05-01", "status": "active"},
    "acct_999": {"plan": "Free", "monthly": 0, "last_charge": None, "status": "active"},
}
_ORDERS = {
    "ord_555": {"item": "Pro annual", "amount": 290, "purchased": "2026-04-15", "refundable": True},
    "ord_777": {"item": "add-on pack", "amount": 49, "purchased": "2026-01-02", "refundable": False},
}

def lookup_account(account_id: str) -> dict:
    rec = _ACCOUNTS.get(account_id)
    return rec | {"account_id": account_id} if rec else {"error": f"unknown account {account_id!r}"}

def lookup_order(order_id: str) -> dict:
    rec = _ORDERS.get(order_id)
    return rec | {"order_id": order_id} if rec else {"error": f"unknown order {order_id!r}"}
```

Tool specs (`LOOKUP_ACCOUNT_SPEC`, `LOOKUP_ORDER_SPEC`) are L0/no-side-effect
JSON-schema dicts of the same shape used by the other examples.

### `_experts.py` — experts + factory

```python
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
    """Load the markdown orchestrator and attach the 3 code-built experts."""
    here = Path(__file__).parent
    return ModiAgent.from_markdown(
        here / "agents" / "triage.md",
        subagents=[billing, technical, refund],
    )
```

Specialists carry no explicit `permission_profile` (harness default applies),
have no delegate tools, and are not in any other agent's `subagents` — so they
are naturally isolated and cannot delegate.

## 5. Live Run Flow (`run.py`) and Delegation-Chain Printing

```python
async def main() -> int:
    console = Console()
    settings = Settings()
    if not settings.model.api_key:
        console.print("[red]Error:[/red] MODI_MODEL_API_KEY not set in .env")
        return 1
    chat_model = create_chat_model(
        provider=settings.model.provider, name=settings.model.name,
        api_key=settings.model.api_key, base_url=settings.model.base_url,
    )

    triage = build_triage_agent()                  # shared single source

    harness = ModiHarness(chat_model=chat_model)
    session = ModiSession(
        harness=harness,
        agents=[triage],                           # only top-level; experts auto-register as nested
        checkpointer=MemorySaver(),
        workspace_root=".modi/workspace",
        memory_root="~/.modi/memory",
        max_steps=20,
    )

    console.print(f"top-level (runnable): {session.list_agents()}")
    console.print(f"all agents (incl. nested): {session.list_all_agents()}")

    ticket = SAMPLE_TICKET   # default billing; comments show how to switch route
    response = session.run_task(
        agent="triage",
        input={"goal": "Resolve the support ticket.",
               "messages": [{"role": "user", "content": ticket}]},
        mode="auto",
    )

    console.print(response["output"])
    print_delegation_chain(console, session, response["thread_id"])
    return 0 if response["status"] == "completed" else 1
```

`print_delegation_chain` walks `session.get_trace(thread_id)` and surfaces the
delegate tool call (`triage ──delegate──▶ <target>`) and any specialist tool
calls (`lookup_account` / `lookup_order`). **Implementation note:** the exact
trace event shape (which payload field carries the tool name) must be verified
against the real `TraceEvent` structure during implementation — the plan will
grep `src/modi_harness` for the trace `tool_call` payload before writing this
function. A reasonable fallback if trace shape differs: derive the route from
`session.get_state(thread_id)["tool_calls"]`.

**Why non-streaming `run_task` instead of `run_streaming`:** the other three
examples all use streaming render. This example uses `run_task` + a post-run
introspection replay to deliberately exercise the introspection API and make the
multi-agent orchestration visible ("what actually happened inside the black
box"). That is this example's differentiator: the others teach "how to run,"
this one teaches "how to see the orchestration."

Three sample tickets (billing / refund / technical) are provided; the default
runs the billing route, with a comment showing which line to change.

## 6. Offline Test (`tests/examples/test_support_triage.py`)

Uses the `_Script` chat model pattern from `tests/subagent/test_e2e.py` (routes
generations by sniffing `AGENT_NAME=` in the prompt) bound to the shared
`build_triage_agent()` tree via `make_session`.

Three tests:

1. **`test_triage_routes_to_refund`** — scripted: triage emits a
   `delegate_to_refund` tool call; the refund specialist (optionally calling
   `lookup_order`) returns; triage summarizes. Asserts `status == "completed"`
   and the refund resolution surfaces in the output.
2. **`test_specialist_isolation`** — `session.list_agents() == ["triage"]`;
   `set(session.list_all_agents()) == {"triage","billing","technical","refund"}`;
   `session.run_task(agent="refund", ...)` raises `AgentNotRegistered`.
3. **`test_delegation_appears_in_trace`** — run the refund route, then assert the
   trace (or state `tool_calls`) contains a `delegate_to_refund` call — guarding
   the `run.py` introspection printing.

These run in the full suite (`tests/examples/` is a new test package).

## 7. README

Top "what it demonstrates" checklist mapped to the gap analysis, then two run
modes:

```markdown
# Support Triage — Multi-Agent Delegation

Demonstrates V0.5 capabilities not shown by the other examples:
- Recursive subagents — a `triage` orchestrator routes tickets to specialists
- `delegate_to_<name>` + `allowed_subagents` governance
- Markdown vs code agents, equivalent — orchestrator is markdown, experts are `ModiAgent(...)`
- Agent isolation — specialists aren't top-level runnable
- Introspection — print the delegation chain from `session.get_trace(...)`
- One agent declaration, two runtimes — `_experts.py` is shared by run.py (live) and the CI test (scripted)

## Run live (needs a model API key)
cp .env.example .env   # fill MODI_MODEL_API_KEY
uv run python examples/support_triage/run.py

## Run offline (CI test, no key)
uv run pytest tests/examples/test_support_triage.py -v
```

The top-level `examples/README.md` index gains a `support_triage` entry.

## 8. Acceptance Criteria

- [ ] `uv run python examples/support_triage/run.py` runs with a key set:
      prints final reply + delegation chain; exits 0 on completed.
- [ ] Without a key: clean error + exit 1, no crash.
- [ ] `tests/examples/test_support_triage.py` — 3 tests pass and join the full
      suite (524 → 527 passed).
- [ ] `_experts.py` agent tree is shared by run.py and the test (defined once).
- [ ] `uv run ruff check` clean on all new files.
- [ ] `examples/README.md` index gains a `support_triage` entry.

## 9. Out of Scope

- `model_override` (future example).
- Streaming render (deliberate non-streaming + introspection).
- Fan-out delegation (this is route-to-one).
- Real external data/services (in-memory fake data only).
- Structured `output_contract` on agents (kept simple; already shown elsewhere).
