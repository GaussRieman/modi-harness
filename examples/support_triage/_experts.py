"""Support-triage example: specialist subagents, their tools, and the
orchestrator factory.

This module is the SINGLE source of agent definitions. Both run.py (live, real
model) and tests/examples/test_support_triage.py (offline, scripted model)
import build_triage_agent() from here — one declaration, two runtimes.
"""

from __future__ import annotations

from pathlib import Path  # noqa: F401  # used by build_triage_agent() in task 2

from modi_harness import ModiAgent, ToolBinding  # noqa: F401  # used in task 2

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
