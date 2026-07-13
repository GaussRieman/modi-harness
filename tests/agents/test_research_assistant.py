from __future__ import annotations

from pathlib import Path

from modi_harness.discovery import discover_agents

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_research_assistant_binds_source_aware_completion_validator() -> None:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    assert [item.id for item in agent.completion_validators] == [
        "validate_research_briefing"
    ]
    validator = agent.completion_validators[0]
    briefing = {
        "research_question": "What changed?",
        "executive_summary": "The cited release changed the runtime.",
        "task_results": [
            {
                "result": "The release uses mandatory Workflows.",
                "evidence": ["https://example.test/release"],
            }
        ],
        "recommendations": [],
        "source_limitations": [],
    }

    assert validator.validate(briefing) is True
    briefing["task_results"][0]["evidence"] = []
    assert validator.validate(briefing) is False
