"""Tests for the unified mode model: auto/preview/trust are the only modes.

The legacy 4-mode names (``ask``/``plan``/``bypass``) were removed in the
intent-aligned runtime redesign (N10). They are no longer accepted anywhere —
``normalize_mode`` rejects them and they are absent from ``PermissionMode``.
"""

from __future__ import annotations

import pytest


def test_literal_is_exactly_the_three_target_modes() -> None:
    from typing import get_args

    from modi_harness.types import PermissionMode

    assert set(get_args(PermissionMode)) == {"auto", "preview", "trust"}


def test_normalize_passes_through_target_names() -> None:
    from modi_harness.policy.modes import normalize_mode

    assert normalize_mode("auto") == "auto"
    assert normalize_mode("preview") == "preview"
    assert normalize_mode("trust") == "trust"


def test_normalize_rejects_removed_legacy_aliases() -> None:
    """ask/plan/bypass are gone — they raise like any other unknown mode."""
    from modi_harness.policy.modes import normalize_mode

    for legacy in ("ask", "plan", "bypass"):
        with pytest.raises(ValueError, match="unknown mode"):
            normalize_mode(legacy)


def test_normalize_unknown_mode_raises() -> None:
    from modi_harness.policy.modes import normalize_mode

    with pytest.raises(ValueError, match="unknown mode"):
        normalize_mode("magic")


def test_normalize_no_warning_for_target_names() -> None:
    """No mode emits a DeprecationWarning anymore — aliases are gone, not deprecated."""
    import warnings

    from modi_harness.policy.modes import normalize_mode

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        normalize_mode("auto")
        normalize_mode("preview")
        normalize_mode("trust")
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations == []
