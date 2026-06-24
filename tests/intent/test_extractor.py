"""Tests for deterministic initial intent extraction (plan N1.2).

Extraction never blocks: even a thin task produces a valid HumanIntentContext.
Clarity *estimation* is N2's job — extraction only sets the deterministic
starting field (goal, confirmed inputs, opening stage, default boundaries).
"""
from __future__ import annotations


def test_thin_input_creates_context_instead_of_blocking() -> None:
    from modi_harness.intent.extractor import extract_intent

    ctx = extract_intent({"prompt": "help me look into something"})

    assert ctx["version"] == 1
    assert ctx["goal"] == "help me look into something"
    # No materials yet → we still need to clarify before exploring.
    assert ctx["current_stage"]["kind"] == "clarify"


def test_empty_input_still_produces_a_context() -> None:
    from modi_harness.intent.extractor import extract_intent

    ctx = extract_intent({})
    assert ctx["goal"]  # falls back to str(payload), never empty-blocks
    assert ctx["current_stage"]["kind"] == "clarify"


def test_research_question_with_sources_opens_in_explore() -> None:
    from modi_harness.intent.extractor import extract_intent

    ctx = extract_intent(
        {
            "research_question": "what is the state of solid-state batteries?",
            "source_urls": ["https://a.example", "https://b.example"],
        }
    )

    assert ctx["goal"] == "what is the state of solid-state batteries?"
    assert ctx["confirmed_inputs"]["source_urls"] == [
        "https://a.example",
        "https://b.example",
    ]
    # Goal + materials present → ready for bounded exploration.
    assert ctx["current_stage"]["kind"] == "explore"


def test_research_question_without_sources_stays_in_clarify() -> None:
    from modi_harness.intent.extractor import extract_intent

    ctx = extract_intent(
        {"research_question": "what is the state of solid-state batteries?"}
    )
    assert ctx["current_stage"]["kind"] == "clarify"


def test_agent_safety_constraints_become_hard_boundaries() -> None:
    from modi_harness.intent.extractor import extract_intent
    from modi_harness.types import AgentProfile

    agent: AgentProfile = {
        "name": "research-assistant",
        "description": "",
        "instruction": "",
        "default_tools": [],
        "default_skills": [],
        "output_contract": None,
        "permission_profile": None,
        "safety_constraints": ["do not invent facts outside provided sources"],
        "tags": [],
        "metadata": {},
    }
    ctx = extract_intent({"prompt": "research X"}, agent=agent)

    statements = [b["statement"] for b in ctx["boundaries"]]
    assert "do not invent facts outside provided sources" in statements
    hard = [b for b in ctx["boundaries"] if b["severity"] == "hard"]
    assert hard, "safety constraints should seed hard boundaries"


def test_explicit_override_replaces_inferred_goal_and_boundaries() -> None:
    from modi_harness.intent.extractor import extract_intent

    override = {
        "goal": "the real goal",
        "boundaries": [
            {
                "id": "b-x",
                "kind": "scope",
                "statement": "stay within EU data",
                "severity": "hard",
                "escalation": "deny",
            }
        ],
    }
    ctx = extract_intent({"prompt": "an inferred goal"}, override=override)

    assert ctx["goal"] == "the real goal"
    assert ctx["boundaries"][0]["id"] == "b-x"
