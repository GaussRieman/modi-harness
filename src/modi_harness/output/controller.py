"""Output Controller implementation.

Free-form pass-through is the default. Structured contracts trigger schema +
required-field + citation + risk-label + forbidden-content checks. All paths
run forbidden-content / prompt-injection / denied-side-effect / security
checks regardless of free_form mode.
"""

from __future__ import annotations

import re
from typing import Any

from jsonschema import Draft202012Validator

from ..types import (
    AgentState,
    OutputContract,
    OutputIssue,
    OutputValidationResult,
)


_UNTRUSTED_TAG = re.compile(r"<\s*/?\s*untrusted\b", re.IGNORECASE)
_SECURITY_KEYWORDS = (
    "disabled rate limiting",
    "bypassed the auth",
    "bypass auth",
    "disabled authentication",
    "removed the security check",
)


class OutputController:
    """Pure validator. Returns OutputValidationResult; never mutates state."""

    def validate(
        self,
        draft_output: Any,
        output_contract: OutputContract,
        state: AgentState,
    ) -> OutputValidationResult:
        issues: list[OutputIssue] = []

        text_view = _to_text(draft_output)

        # Common checks (run for both free-form and structured).
        issues.extend(_check_forbidden(text_view, output_contract))
        issues.extend(_check_prompt_injection(text_view))
        issues.extend(_check_security_keywords(text_view))
        issues.extend(_check_denied_side_effect(text_view, state))

        if not output_contract["free_form"]:
            issues.extend(_check_schema(draft_output, output_contract))
            issues.extend(_check_required_fields(draft_output, output_contract))
            if output_contract["citation_required"]:
                issues.extend(_check_citations(draft_output))
            if output_contract["risk_label_required"]:
                issues.extend(_check_risk_label(draft_output))

        # Decide status.
        if any(i["severity"] == "error" for i in issues):
            status = "rejected"
            output: dict[str, Any] | None = None
        elif output_contract["review_required"]:
            status = "needs_review"
            output = _to_dict(draft_output)
        else:
            status = "validated"
            output = _to_dict(draft_output)

        return OutputValidationResult(  # type: ignore[typeddict-item]
            status=status,  # type: ignore[arg-type]
            output=output,
            issues=issues,
            required_action=None,
        )


# ----------------------------------------------------------------------
# checks
# ----------------------------------------------------------------------


def _check_forbidden(text: str, contract: OutputContract) -> list[OutputIssue]:
    issues: list[OutputIssue] = []
    for pattern in contract["forbidden_patterns"]:
        if pattern.lower() in text.lower():
            issues.append(
                OutputIssue(
                    code="forbidden_content",
                    severity="error",
                    field=None,
                    message=f"output contains forbidden pattern: {pattern!r}",
                    hint="remove or redact the matching content",
                )
            )
    return issues


def _check_prompt_injection(text: str) -> list[OutputIssue]:
    if _UNTRUSTED_TAG.search(text):
        return [
            OutputIssue(
                code="prompt_injection_warning",
                severity="error",
                field=None,
                message="output contains <untrusted> tag fragment; cannot leak wrapper to user",
                hint="strip wrapper tags before returning to user",
            )
        ]
    return []


def _check_security_keywords(text: str) -> list[OutputIssue]:
    lower = text.lower()
    for kw in _SECURITY_KEYWORDS:
        if kw in lower:
            return [
                OutputIssue(
                    code="security_authorization_missing",
                    severity="error",
                    field=None,
                    message=f"output claims security-sensitive action without authorization: {kw!r}",
                    hint=None,
                )
            ]
    return []


def _check_denied_side_effect(text: str, state: AgentState) -> list[OutputIssue]:
    """If a denied tool name appears in past-tense success language, flag it."""
    issues: list[OutputIssue] = []
    lower = text.lower()
    completed_markers = ("i have ", "i've ", "completed", "successfully")
    for denied in state["denied_actions"]:
        # Use the action verb embedded in the tool name. e.g. send_email -> sent
        verb = _verb_from_tool_name(denied["tool_name"])
        if verb and verb in lower and any(m in lower for m in completed_markers):
            issues.append(
                OutputIssue(
                    code="denied_side_effect_claimed",
                    severity="error",
                    field=None,
                    message=f"output claims completion of denied action: {denied['tool_name']}",
                    hint=None,
                )
            )
            break
    return issues


def _check_schema(value: Any, contract: OutputContract) -> list[OutputIssue]:
    schema = contract["schema"]
    if not schema or not isinstance(value, dict):
        return []
    issues: list[OutputIssue] = []
    validator = Draft202012Validator(schema)
    for err in validator.iter_errors(value):
        if err.validator == "required":
            for missing in err.message.split("'")[1::2]:
                issues.append(
                    OutputIssue(
                        code="schema.missing_field",
                        severity="error",
                        field=missing,
                        message=f"missing required field {missing!r}",
                        hint=None,
                    )
                )
        elif err.validator in ("type", "enum", "format"):
            field = err.path[-1] if err.path else None
            issues.append(
                OutputIssue(
                    code="schema.type_mismatch",
                    severity="error",
                    field=str(field) if field is not None else None,
                    message=err.message,
                    hint=None,
                )
            )
        else:
            issues.append(
                OutputIssue(
                    code="schema.type_mismatch",
                    severity="error",
                    field=None,
                    message=err.message,
                    hint=None,
                )
            )
    return issues


def _check_required_fields(value: Any, contract: OutputContract) -> list[OutputIssue]:
    if not isinstance(value, dict):
        return [
            OutputIssue(
                code="schema.type_mismatch",
                severity="error",
                field=None,
                message="structured output must be a JSON object",
                hint=None,
            )
        ]
    issues: list[OutputIssue] = []
    for field in contract["required_fields"]:
        if field not in value:
            issues.append(
                OutputIssue(
                    code="schema.missing_field",
                    severity="error",
                    field=field,
                    message=f"missing required field {field!r}",
                    hint=None,
                )
            )
    return issues


def _check_citations(value: Any) -> list[OutputIssue]:
    if isinstance(value, dict):
        cites = value.get("citations") or value.get("evidence")
        if cites:
            return []
    return [
        OutputIssue(
            code="citation.missing",
            severity="error",
            field=None,
            message="output is missing required citations / evidence",
            hint="include a 'citations' or 'evidence' field with at least one entry",
        )
    ]


def _check_risk_label(value: Any) -> list[OutputIssue]:
    if isinstance(value, dict) and value.get("risk_label"):
        return []
    return [
        OutputIssue(
            code="risk_label.missing",
            severity="error",
            field="risk_label",
            message="output is missing required risk_label",
            hint=None,
        )
    ]


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


_TOOL_VERB_MAP: dict[str, str] = {
    "send_email": "sent",
    "send_message": "sent",
    "create": "created",
    "delete": "deleted",
    "post": "posted",
    "publish": "published",
}


def _verb_from_tool_name(tool_name: str) -> str | None:
    if tool_name in _TOOL_VERB_MAP:
        return _TOOL_VERB_MAP[tool_name]
    for prefix, verb in _TOOL_VERB_MAP.items():
        if tool_name.startswith(prefix + "_") or tool_name.startswith(prefix):
            return verb
    return None


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        import json

        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"value": value}
