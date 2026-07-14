"""Built-in policy rule packs.

A rule pack exposes ``matchers() -> list[ActionMatcher]``. Matchers may
elevate a base decision (e.g. allow -> require_approval) but never lower it.
The ``core`` pack is always included implicitly.
"""

from __future__ import annotations

from collections.abc import Callable

from ..types import ActionMatcher

# Each rule pack is a callable returning a list of matchers.
RulePack = Callable[[], list[ActionMatcher]]


def _core_matchers() -> list[ActionMatcher]:
    # denied-retry / destructive-without-authorization / security-abuse are
    # implemented directly in PolicyGate (not as matchers) because they require
    # state inspection beyond a static matcher signature.
    return []


def _coding_matchers() -> list[ActionMatcher]:
    # Deny common git-mutation tool names regardless of declared risk level.
    return [
        ActionMatcher(
            kind="tool_call",
            tool_name_pattern=name,
            argument_predicate=None,
            risk_floor=None,
            tag_any=[],
            elevate_to="deny",
            audit_label="coding",
        )
        for name in ("git_push", "git_force_push", "git_tag", "git_reset_hard")
    ]


def _messaging_matchers() -> list[ActionMatcher]:
    return [
        ActionMatcher(
            kind="tool_call",
            tool_name_pattern=None,
            argument_predicate=None,
            risk_floor="L4",
            tag_any=["messaging", "broadcast"],
            elevate_to="require_approval",
            audit_label="messaging",
        )
    ]


def _finance_matchers() -> list[ActionMatcher]:
    return [
        ActionMatcher(
            kind="tool_call",
            tool_name_pattern=None,
            argument_predicate=None,
            risk_floor="L3",
            tag_any=["finance", "payment"],
            elevate_to="require_approval",
            audit_label="finance",
        )
    ]


BUILTIN_PACKS: dict[str, RulePack] = {
    "core": _core_matchers,
    "coding": _coding_matchers,
    "messaging": _messaging_matchers,
    "finance": _finance_matchers,
}


def load_packs(names: list[str]) -> list[tuple[str, ActionMatcher]]:
    """Load matchers from the listed packs. ``core`` is always included."""
    final_names = list(names)
    if "core" not in final_names:
        final_names = ["core", *final_names]

    out: list[tuple[str, ActionMatcher]] = []
    for name in final_names:
        pack = BUILTIN_PACKS.get(name)
        if pack is None:
            raise ValueError(f"unknown rule pack: {name}")
        for matcher in pack():
            out.append((name, matcher))
    return out
