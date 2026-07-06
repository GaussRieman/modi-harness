from __future__ import annotations

from typing import get_type_hints


def _fields(typed_dict: object) -> set[str]:
    return set(get_type_hints(typed_dict))


def test_intent_aligned_core_contract_fields_are_stable() -> None:
    from modi_harness.actions import ActionImpact, ActionProposal
    from modi_harness.alignment import AlignmentDecision
    from modi_harness.intent import HumanIntentContext
    from modi_harness.trace.lineage import IntentLineage
    from modi_harness.types import PendingJudgment

    assert {
        "version",
        "goal",
        "desired_outcome",
        "boundaries",
        "non_goals",
        "success_criteria",
        "current_stage",
        "responsibility",
        "escalation",
        "tradeoffs",
        "confirmed_inputs",
        "decisions",
        "corrections",
    } <= _fields(HumanIntentContext)
    assert {
        "id",
        "kind",
        "summary",
        "tool_name",
        "arguments",
        "intent_version",
        "stage_id",
        "expected_outcome",
        "impact",
    } <= _fields(ActionProposal)
    assert {
        "risk_level",
        "side_effect",
        "external_commitment",
        "irreversible",
        "user_visible_state_changes",
        "changes_scope_or_goal",
        "sensitive_data",
        "cost_impact",
    } <= _fields(ActionImpact)
    assert {
        "id",
        "decision",
        "reason",
        "action_id",
        "intent_version",
        "stage_id",
        "boundary_hits",
        "governance_requirements",
        "model_judged",
    } <= _fields(AlignmentDecision)
    assert {
        "judgment_id",
        "approval_id",
        "tool_call_id",
        "target_action_id",
        "target_stage_id",
        "reviewed_action_hash",
        "prompt",
        "allowed_kinds",
        "proposed_intent_patch",
        "summary",
        "rationale",
        "risk_level",
        "requested_at",
    } <= _fields(PendingJudgment)
    assert {
        "action_id",
        "alignment_decision_id",
        "intent_version",
        "stage_id",
        "judgment_id",
        "boundary_hits",
    } <= _fields(IntentLineage)


def test_run_end_summary_contract_is_documented_by_eval_helper() -> None:
    from modi_harness._test_fixtures import stable_trace_contract

    run_id = "run-contract"
    contract = stable_trace_contract([
        {
            "event_type": "run_end",
            "payload": {
                "step_id": "run-end-0001",
                "previous_step_id": "output-0001",
                "model_calls": 1,
                "model_usage": {"total_tokens": 0},
                "model_latency_ms": 0,
                "tool_attempts": 2,
                "tool_failures": 1,
                "tool_latency_ms": 3,
            },
            "run_id": run_id,
        }
    ])

    assert contract["run_end"] == {
        "step_id": "run-end-0001",
        "previous_step_id": "output-0001",
        "model_calls": 1,
        "model_usage_total_tokens_min": ">=0",
        "model_latency_ms_min": ">=0",
        "tool_attempts": 2,
        "tool_failures": 1,
        "tool_latency_ms_min": ">=0",
    }
