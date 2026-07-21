"""Pure executor contracts and root-authoritative human decision helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Generic, TypeVar, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]

from .._utils import compute_fingerprint
from ..workflow.components import PinnedComponent
from .types import PendingGoalDecision, PendingTaskDecision
from .verification import json_value


class ExecutorContractError(ValueError):
    """A pinned executor contract or response is invalid."""


class PendingDecisionError(ValueError):
    """A root-authoritative pending decision cannot be consumed."""


class PendingDecisionConflict(PendingDecisionError):
    """A consumed request ID was reused with a different exact response."""


class PendingDecisionStale(PendingDecisionError):
    """A response targets a root or graph revision that is no longer current."""


@dataclass(frozen=True, slots=True)
class PinnedHumanTaskContract:
    """Validated view of one execution-contract-pinned human Task component."""

    id: str
    version: str
    fingerprint: str
    prompt_schema: Mapping[str, Any]
    response_schema: Mapping[str, Any]
    decision_class: str
    allowed_decisions: tuple[str, ...]
    authority_requirement: Any
    timeout_behavior: str
    resume_policy: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt_schema", _freeze_mapping(self.prompt_schema))
        object.__setattr__(self, "response_schema", _freeze_mapping(self.response_schema))
        object.__setattr__(
            self,
            "authority_requirement",
            _freeze(self.authority_requirement),
        )

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], json_value(self))


@dataclass(frozen=True, slots=True)
class ValidatedHumanResponse:
    decision: str
    response: Mapping[str, Any]
    response_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "response", _freeze_mapping(self.response))

    @property
    def envelope(self) -> Mapping[str, Any]:
        return _freeze_mapping(
            {
                "decision": self.decision,
                "response": self.response,
            }
        )


DecisionT = TypeVar("DecisionT", PendingTaskDecision, PendingGoalDecision)


@dataclass(frozen=True, slots=True)
class DecisionConsumption(Generic[DecisionT]):
    decision: DecisionT
    replayed: bool


def parse_human_task_contract(component: PinnedComponent) -> PinnedHumanTaskContract:
    """Parse and validate the configuration of one pinned human contract."""

    if component.kind != "human_contract":
        raise ExecutorContractError("human Task binding requires a human_contract component")
    configuration = component.configuration
    prompt_schema = _required_mapping(configuration, "prompt_schema")
    response_schema = _required_mapping(configuration, "response_schema")
    decision_class = _required_string(configuration, "decision_class")
    if decision_class not in {"interaction", "judgment"}:
        raise ExecutorContractError(
            f"unsupported human decision class {decision_class!r}"
        )
    raw_allowed = configuration.get("allowed_decisions")
    if not isinstance(raw_allowed, tuple | list):
        raise ExecutorContractError("allowed_decisions must be an array")
    allowed = tuple(
        dict.fromkeys(
            str(item).strip() for item in raw_allowed if str(item).strip()
        )
    )
    if not allowed:
        raise ExecutorContractError("allowed_decisions cannot be empty")
    timeout_behavior = _required_string(configuration, "timeout_behavior")
    resume_policy = _required_string(configuration, "resume_policy")
    try:
        Draft202012Validator.check_schema(json_value(prompt_schema))
        Draft202012Validator.check_schema(json_value(response_schema))
    except SchemaError as exc:
        raise ExecutorContractError(f"human contract has an invalid JSON Schema: {exc.message}") from exc
    return PinnedHumanTaskContract(
        id=component.id,
        version=component.version,
        fingerprint=component.fingerprint,
        prompt_schema=prompt_schema,
        response_schema=response_schema,
        decision_class=decision_class,
        allowed_decisions=allowed,
        authority_requirement=json_value(configuration.get("authority_requirement")),
        timeout_behavior=timeout_behavior,
        resume_policy=resume_policy,
    )


def validate_human_prompt(
    contract: PinnedHumanTaskContract,
    prompt: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate and freeze the exact rendered prompt persisted at the root."""

    return _validate_payload(contract.prompt_schema, prompt, label="human Task prompt")


def validate_human_response(
    contract: PinnedHumanTaskContract,
    response: Mapping[str, Any],
    *,
    decision: str | None = None,
) -> ValidatedHumanResponse:
    """Validate one response payload and its allowed decision class."""

    payload = _validate_payload(
        contract.response_schema,
        response,
        label="human Task response",
    )
    embedded = payload.get("decision", payload.get("kind"))
    if decision is not None and embedded is not None and str(embedded) != decision:
        raise ExecutorContractError("human response decision conflicts with its payload")
    selected = str(decision if decision is not None else embedded or "").strip()
    if selected not in contract.allowed_decisions:
        raise ExecutorContractError(
            f"human response decision {selected!r} is not allowed by the pinned contract"
        )
    envelope = {"decision": selected, "response": json_value(payload)}
    return ValidatedHumanResponse(
        decision=selected,
        response=payload,
        response_hash=compute_fingerprint(envelope),
    )


def consume_pending_task_decision(
    pending: PendingTaskDecision,
    *,
    response: Mapping[str, Any],
    observed_root_revision: int,
    observed_graph_revision: int,
    commit_root_revision: int,
) -> DecisionConsumption[PendingTaskDecision]:
    """Consume or replay one exact Task decision without external mutation."""

    consumed, replayed = _consume(
        pending,
        response=response,
        observed_root_revision=observed_root_revision,
        observed_graph_revision=observed_graph_revision,
        commit_root_revision=commit_root_revision,
    )
    return DecisionConsumption(cast(PendingTaskDecision, consumed), replayed)


def consume_pending_goal_decision(
    pending: PendingGoalDecision,
    *,
    response: Mapping[str, Any],
    observed_root_revision: int,
    observed_graph_revision: int,
    commit_root_revision: int,
) -> DecisionConsumption[PendingGoalDecision]:
    """Consume or replay one exact Goal decision without external mutation."""

    consumed, replayed = _consume(
        pending,
        response=response,
        observed_root_revision=observed_root_revision,
        observed_graph_revision=observed_graph_revision,
        commit_root_revision=commit_root_revision,
    )
    return DecisionConsumption(cast(PendingGoalDecision, consumed), replayed)


def _consume(
    pending: PendingTaskDecision | PendingGoalDecision,
    *,
    response: Mapping[str, Any],
    observed_root_revision: int,
    observed_graph_revision: int,
    commit_root_revision: int,
) -> tuple[PendingTaskDecision | PendingGoalDecision, bool]:
    response_value = cast(dict[str, Any], json_value(response))
    response_hash = compute_fingerprint(response_value)
    if pending.status == "consumed":
        if pending.response_hash != response_hash:
            raise PendingDecisionConflict(
                f"decision request {pending.request_id!r} already consumed a different response"
            )
        return pending, True
    if pending.status != "pending":
        raise PendingDecisionError(
            f"decision request {pending.request_id!r} has invalid status {pending.status!r}"
        )
    _validate_pending_identity(pending)
    if observed_root_revision != pending.expected_root_revision:
        raise PendingDecisionStale(
            f"decision expects root revision {pending.expected_root_revision}; "
            f"observed {observed_root_revision}"
        )
    if observed_graph_revision != pending.graph_revision:
        raise PendingDecisionStale(
            f"decision expects graph revision {pending.graph_revision}; "
            f"observed {observed_graph_revision}"
        )
    if commit_root_revision <= observed_root_revision:
        raise PendingDecisionStale(
            "decision consumption root revision must advance monotonically"
        )
    decision = _response_decision(response_value)
    if decision not in pending.allowed_decisions:
        raise PendingDecisionError(
            f"decision {decision!r} is not allowed for request {pending.request_id!r}"
        )
    return (
        replace(
            pending,
            status="consumed",
            response_hash=response_hash,
            response=response_value,
            consumed_root_revision=commit_root_revision,
        ),
        False,
    )


def _validate_pending_identity(
    pending: PendingTaskDecision | PendingGoalDecision,
) -> None:
    for label, value in (
        ("request_id", pending.request_id),
        ("input_hash", pending.input_hash),
    ):
        if not value.strip():
            raise PendingDecisionError(f"pending decision {label} must be non-empty")
    if pending.expected_root_revision < 0 or pending.graph_revision < 0:
        raise PendingDecisionError("pending decision revisions cannot be negative")
    if not pending.allowed_decisions:
        raise PendingDecisionError("pending decision allowed_decisions cannot be empty")
    if isinstance(pending, PendingTaskDecision):
        for label, value in (
            ("attempt_id", pending.attempt_id),
            ("contract_id", pending.contract_id),
            ("contract_fingerprint", pending.contract_fingerprint),
        ):
            if not value.strip():
                raise PendingDecisionError(
                    f"pending Task decision {label} must be non-empty"
                )
    elif not pending.goal_verification_record_id.strip():
        raise PendingDecisionError(
            "pending Goal decision verification record must be non-empty"
        )


def _response_decision(response: Mapping[str, Any]) -> str:
    explicit = response.get("decision")
    alias = response.get("kind")
    if explicit is not None and alias is not None and str(explicit) != str(alias):
        raise PendingDecisionError("response decision and kind conflict")
    value = str(explicit if explicit is not None else alias or "").strip()
    if not value:
        raise PendingDecisionError("response must include decision or kind")
    return value


def _validate_payload(
    schema: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    label: str,
) -> Mapping[str, Any]:
    value = cast(dict[str, Any], json_value(payload))
    try:
        Draft202012Validator(json_value(schema)).validate(value)
    except ValidationError as exc:
        raise ExecutorContractError(f"{label} failed schema validation: {exc.message}") from exc
    return _freeze_mapping(value)


def _required_mapping(
    source: Mapping[str, Any],
    key: str,
) -> Mapping[str, Any]:
    value = source.get(key)
    if not isinstance(value, Mapping):
        raise ExecutorContractError(f"human contract {key} must be a mapping")
    return cast(Mapping[str, Any], value)


def _required_string(source: Mapping[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExecutorContractError(f"human contract {key} must be non-empty")
    return value.strip()


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, tuple | list):
        return tuple(_freeze(item) for item in value)
    return value


__all__ = [
    "DecisionConsumption",
    "ExecutorContractError",
    "PendingDecisionConflict",
    "PendingDecisionError",
    "PendingDecisionStale",
    "PinnedHumanTaskContract",
    "ValidatedHumanResponse",
    "consume_pending_goal_decision",
    "consume_pending_task_decision",
    "parse_human_task_contract",
    "validate_human_prompt",
    "validate_human_response",
]
