"""Pinned component invocation helpers for the long-task parent runtime."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from typing import Any, cast

from .._utils import compute_fingerprint, new_ulid
from ..workflow.components import PinnedComponent
from .types import ComponentInvocationKind, DurableComponentInvocation, VerificationRecord


class ComponentExecutionError(RuntimeError):
    """A pinned Planner/Context Builder/Verifier call was invalid."""


def invoke_component(
    component: PinnedComponent,
    *,
    invocation: DurableComponentInvocation,
    inputs: Mapping[str, Any],
) -> tuple[Any, DurableComponentInvocation]:
    implementation = component.implementation
    if implementation is None:
        raise ComponentExecutionError(f"component {component.id!r} is not executable")
    input_payload = json_value(inputs)
    input_hash = compute_fingerprint(input_payload)
    if invocation.status != "prepared":
        raise ComponentExecutionError("component invocation is not prepared")
    if (
        invocation.component_id != component.id
        or invocation.component_fingerprint != component.fingerprint
        or invocation.input_hash != input_hash
    ):
        raise ComponentExecutionError("prepared component invocation binding changed")
    try:
        signature = inspect.signature(implementation)
        accepts_key = "idempotency_key" in signature.parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if not accepts_key:
            raise ComponentExecutionError(
                f"component {component.id!r} must accept idempotency_key"
            )
        output = implementation(
            input_payload,
            idempotency_key=invocation.idempotency_key,
        )
        output_hash = compute_fingerprint(json_value(output))
    except Exception as exc:
        raise ComponentExecutionError(f"component {component.id!r} failed: {exc}") from exc
    return output, replace(
        invocation,
        status="completed",
        output_hash=output_hash,
    )


def prepare_component_invocation(
    component: PinnedComponent,
    *,
    kind: ComponentInvocationKind,
    idempotency_key: str,
    inputs: Mapping[str, Any],
) -> DurableComponentInvocation:
    return DurableComponentInvocation(
        invocation_id=new_ulid(),
        kind=kind,
        component_id=component.id,
        component_fingerprint=component.fingerprint,
        idempotency_key=idempotency_key,
        input_hash=compute_fingerprint(json_value(inputs)),
        status="prepared",
    )


def verifier_outcome(component: PinnedComponent, output: Any) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(output, Mapping):
        raise ComponentExecutionError(
            f"verifier component {component.id!r} must return a mapping"
        )
    outcome = output.get("outcome")
    if not isinstance(outcome, str) or outcome not in component.supported_outcomes:
        raise ComponentExecutionError(
            f"verifier component {component.id!r} returned unsupported outcome {outcome!r}"
        )
    return outcome, cast(Mapping[str, Any], output)


def verification_record(
    *,
    component: PinnedComponent,
    invocation: DurableComponentInvocation,
    submission_id: str,
    target_ref: str,
    outcome: str,
    result: Mapping[str, Any],
) -> VerificationRecord:
    """Build one durable verifier decision bound to its exact invocation."""

    return VerificationRecord(
        record_id=new_ulid(),
        kind="task",
        target_ref=target_ref,
        component_fingerprint=component.fingerprint,
        input_hash=invocation.input_hash,
        status=cast(Any, _verification_status(outcome)),
        evidence_refs=tuple(result.get("evidence_refs") or ()),
        artifact_refs=tuple(result.get("artifact_refs") or ()),
        reason=cast(str | None, result.get("reason")),
        submission_id=submission_id,
        validator_id=component.id,
        validator_version=component.version,
        invocation_id=invocation.invocation_id,
        output_hash=invocation.output_hash,
        outcome=outcome,
    )


def _verification_status(outcome: str) -> str:
    return {
        "passed": "passed",
        "repairable": "repairable",
        "repairable_gap": "repairable",
        "needs_replan": "needs_replan",
        "ambiguous": "ambiguous",
        "impossible": "terminal",
        "terminal": "terminal",
    }[outcome]


def json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: json_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [json_value(item) for item in value]
    return value


__all__ = [
    "ComponentExecutionError",
    "invoke_component",
    "json_value",
    "prepare_component_invocation",
    "verification_record",
    "verifier_outcome",
]
