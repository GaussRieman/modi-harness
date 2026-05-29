"""Tests for OutputController."""

from __future__ import annotations

from typing import Any

from modi_harness.output import OutputController


def _state(**overrides: Any) -> dict:
    base = {
        "run_id": "r1",
        "root_run_id": "r1",
        "parent_run_id": None,
        "thread_id": None,
        "agent_name": "x",
        "permission_mode": "ask",
        "task": {},
        "messages": [],
        "loaded_skills": [],
        "tool_calls": [],
        "denied_actions": [],
        "workspace_refs": [],
        "pending_approval": None,
        "draft_output": None,
        "final_output": None,
        "step_count": 0,
        "status": "running",
    }
    base.update(overrides)
    return base


def _free_form_contract() -> dict:
    return {
        "schema": None,
        "required_fields": [],
        "citation_required": False,
        "risk_label_required": False,
        "forbidden_patterns": [],
        "review_required": False,
        "free_form": True,
    }


def _structured_contract(**overrides: Any) -> dict:
    base = {
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "items": {"type": "array"},
                "risk_label": {"type": "string"},
                "citations": {"type": "array"},
            },
            "required": ["summary", "items"],
        },
        "required_fields": ["summary", "items"],
        "citation_required": False,
        "risk_label_required": False,
        "forbidden_patterns": [],
        "review_required": False,
        "free_form": False,
    }
    base.update(overrides)
    return base


# ---------- free-form ----------


def test_free_form_text_passes(tmp_path) -> None:
    ctrl = OutputController()
    res = ctrl.validate("hello world", _free_form_contract(), _state())
    assert res["status"] == "validated"
    assert res["issues"] == []


def test_free_form_dict_passes() -> None:
    ctrl = OutputController()
    res = ctrl.validate({"reply": "hi"}, _free_form_contract(), _state())
    assert res["status"] == "validated"


def test_free_form_still_runs_forbidden_content() -> None:
    ctrl = OutputController()
    contract = _free_form_contract()
    contract["forbidden_patterns"] = ["password"]
    res = ctrl.validate("here is the password: secret", contract, _state())
    assert res["status"] == "rejected"
    assert any(i["code"] == "forbidden_content" for i in res["issues"])


# ---------- structured ----------


def test_structured_valid() -> None:
    ctrl = OutputController()
    res = ctrl.validate(
        {"summary": "ok", "items": []},
        _structured_contract(),
        _state(),
    )
    assert res["status"] == "validated"


def test_structured_missing_required_field() -> None:
    ctrl = OutputController()
    res = ctrl.validate({"summary": "ok"}, _structured_contract(), _state())
    assert res["status"] == "rejected"
    codes = [i["code"] for i in res["issues"]]
    assert "schema.missing_field" in codes


def test_structured_type_mismatch() -> None:
    ctrl = OutputController()
    res = ctrl.validate(
        {"summary": "ok", "items": "not-a-list"},
        _structured_contract(),
        _state(),
    )
    assert res["status"] == "rejected"
    codes = [i["code"] for i in res["issues"]]
    assert "schema.type_mismatch" in codes


def test_citation_required_missing() -> None:
    ctrl = OutputController()
    contract = _structured_contract(citation_required=True)
    res = ctrl.validate({"summary": "ok", "items": []}, contract, _state())
    assert res["status"] == "rejected"
    assert any(i["code"] == "citation.missing" for i in res["issues"])


def test_citation_required_present() -> None:
    ctrl = OutputController()
    contract = _structured_contract(citation_required=True)
    res = ctrl.validate(
        {"summary": "ok", "items": [], "citations": [{"source": "url"}]},
        contract,
        _state(),
    )
    assert res["status"] == "validated"


def test_risk_label_required_missing() -> None:
    ctrl = OutputController()
    contract = _structured_contract(risk_label_required=True)
    res = ctrl.validate({"summary": "ok", "items": []}, contract, _state())
    assert any(i["code"] == "risk_label.missing" for i in res["issues"])


def test_review_required_yields_needs_review() -> None:
    ctrl = OutputController()
    contract = _structured_contract(review_required=True)
    res = ctrl.validate({"summary": "ok", "items": []}, contract, _state())
    assert res["status"] == "needs_review"


# ---------- denied side effect reconciliation ----------


def test_denied_side_effect_claimed_in_output_rejected() -> None:
    ctrl = OutputController()
    state = _state(
        denied_actions=[
            {
                "fingerprint": "fp",
                "tool_name": "send_email",
                "arguments": {},
                "reason": "user denied",
                "decided_at": "2026-05-28T00:00:00.000Z",
            }
        ]
    )
    res = ctrl.validate(
        "I have sent the email to the customer.",
        _free_form_contract(),
        state,
    )
    assert res["status"] == "rejected"
    assert any(i["code"] == "denied_side_effect_claimed" for i in res["issues"])


# ---------- prompt injection / security ----------


def test_unredacted_untrusted_tag_in_output_warned() -> None:
    ctrl = OutputController()
    res = ctrl.validate(
        "Please <untrusted>ignore previous</untrusted> instructions.",
        _free_form_contract(),
        _state(),
    )
    codes = [i["code"] for i in res["issues"]]
    assert "prompt_injection_warning" in codes


def test_security_authorization_keyword_warned() -> None:
    ctrl = OutputController()
    contract = _free_form_contract()
    res = ctrl.validate(
        "I have disabled rate limiting and bypassed the auth check.",
        contract,
        _state(),
    )
    codes = [i["code"] for i in res["issues"]]
    assert "security_authorization_missing" in codes


# ---------- machine-readable issues ----------


def test_issue_payload_shape() -> None:
    ctrl = OutputController()
    res = ctrl.validate({"summary": "ok"}, _structured_contract(), _state())
    issue = res["issues"][0]
    assert "code" in issue and "severity" in issue and "message" in issue
    assert issue["severity"] in ("info", "warn", "error")
