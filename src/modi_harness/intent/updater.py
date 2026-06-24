"""Apply a :class:`HumanJudgment` to the intent field (plan N6.2).

Human participation in the intent-aligned runtime is *judgment*, not approval:
a human can approve, but can equally revise the goal, add a boundary, redirect
the stage, or confirm an input. Each judgment may carry an
:class:`IntentPatch`; applying it produces a new ``HumanIntentContext`` with a
bumped version, the judgment recorded in ``decisions``, and — for judgments that
correct drift (revise/redirect/constrain) — a ``corrections`` entry.

The updater never mutates its input. After applying a judgment the caller
recomputes clarity and autonomy via :func:`recompute_autonomy`, because a new
goal or boundary changes how operational the intent is and how much freedom the
agent has.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .._utils import new_ulid, now_iso
from ..autonomy.scope import AutonomyScope, derive_autonomy_scope
from .clarity import ClarityEstimator, estimate_clarity, run_estimator
from .types import (
    HumanIntentContext,
    HumanJudgment,
    IntentClarity,
    IntentCorrection,
    IntentPatch,
)

# Judgment kinds that record a correction — the human is steering the field back
# toward what they meant, which is worth an auditable drift record.
_CORRECTING_KINDS = frozenset({"revise", "redirect", "constrain"})


def apply_judgment(
    intent: HumanIntentContext, judgment: HumanJudgment
) -> HumanIntentContext:
    """Return a new intent with the judgment's patch applied and version bumped.

    The input is left unmodified. ``decisions`` always gains the judgment;
    ``corrections`` gains an entry for drift-correcting kinds.
    """
    out = copy.deepcopy(intent)
    _apply_patch(out, judgment.get("intent_updates") or {})

    out["decisions"] = [*out["decisions"], copy.deepcopy(judgment)]
    if judgment["kind"] in _CORRECTING_KINDS:
        out["corrections"] = [*out["corrections"], _correction_from(judgment)]
    out["version"] = intent["version"] + 1
    return out


def recompute_autonomy(
    intent: HumanIntentContext,
    *,
    estimator: ClarityEstimator | None = None,
    task: Mapping[str, Any] | None = None,
) -> tuple[IntentClarity, AutonomyScope]:
    """Recompute clarity and the enforced autonomy scope after a judgment.

    Model-first when an ``estimator`` is injected; otherwise the deterministic
    cold-start clarity. The scope is always derived from the (clamped) clarity
    and the active boundaries, so a newly added hard/deny boundary immediately
    constrains autonomy.
    """
    verdict = run_estimator(estimator, intent, task or {}) if estimator else None
    clarity = estimate_clarity(intent, verdict)
    scope = derive_autonomy_scope(clarity, intent)
    return clarity, scope


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _apply_patch(intent: HumanIntentContext, patch: IntentPatch) -> None:
    """Apply present patch keys in place to an already-copied intent."""
    if "goal" in patch:
        intent["goal"] = patch["goal"]
    if "desired_outcome" in patch:
        intent["desired_outcome"] = patch["desired_outcome"]
    if "set_stage" in patch:
        intent["current_stage"] = copy.deepcopy(patch["set_stage"])

    if patch.get("add_boundaries"):
        intent["boundaries"] = [
            *intent["boundaries"],
            *copy.deepcopy(patch["add_boundaries"]),
        ]
    if patch.get("remove_boundary_ids"):
        drop = set(patch["remove_boundary_ids"])
        intent["boundaries"] = [b for b in intent["boundaries"] if b["id"] not in drop]

    if patch.get("add_non_goals"):
        intent["non_goals"] = [*intent["non_goals"], *patch["add_non_goals"]]
    if patch.get("add_success_criteria"):
        intent["success_criteria"] = [
            *intent["success_criteria"],
            *patch["add_success_criteria"],
        ]

    if patch.get("confirmed_inputs"):
        intent["confirmed_inputs"] = {
            **intent["confirmed_inputs"],
            **patch["confirmed_inputs"],
        }
    if patch.get("tradeoffs"):
        intent["tradeoffs"] = {**intent["tradeoffs"], **patch["tradeoffs"]}


def _correction_from(judgment: HumanJudgment) -> IntentCorrection:
    summary = judgment.get("rationale") or f"{judgment['kind']} judgment"
    return IntentCorrection(
        id=new_ulid(),
        created_at=judgment.get("created_at") or now_iso(),
        summary=summary,
        detail=judgment.get("rationale"),
    )


__all__ = ["apply_judgment", "recompute_autonomy"]
