"""Tests for the unified mode model: preview/trust as new names; ask/plan/bypass as deprecation aliases."""
from __future__ import annotations

import pytest


def test_new_literal_accepts_preview_and_trust() -> None:
    from modi_harness.types import PermissionMode  # noqa
    # type-level only; runtime check below
    valid = {"ask", "auto", "plan", "bypass", "preview", "trust"}
    from typing import get_args
    actual = set(get_args(PermissionMode))
    assert valid <= actual


def test_normalize_aliases_preview_to_preview_etc() -> None:
    """Legacy names map onto target names via a single normalizer."""
    from modi_harness.policy.modes import normalize_mode
    assert normalize_mode("plan") == "preview"
    assert normalize_mode("bypass") == "trust"
    # 'ask' and 'auto' both map to 'auto' in the target model — TTY decides
    # which sub-behavior they get at policy time. The legacy distinction is
    # preserved via the `interactive` flag, set elsewhere.
    assert normalize_mode("ask") == "auto"
    assert normalize_mode("auto") == "auto"
    assert normalize_mode("preview") == "preview"
    assert normalize_mode("trust") == "trust"


def test_normalize_unknown_mode_raises() -> None:
    from modi_harness.policy.modes import normalize_mode
    with pytest.raises(ValueError, match="unknown mode"):
        normalize_mode("magic")


def test_legacy_ask_emits_deprecation_warning() -> None:
    """Using legacy names emits a DeprecationWarning so callers can migrate."""
    import warnings
    from modi_harness.policy.modes import normalize_mode

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        normalize_mode("ask")
        normalize_mode("plan")
        normalize_mode("bypass")
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 3, [str(w.message) for w in caught]


def test_normalize_no_warning_for_target_names() -> None:
    """Target names ('auto', 'preview', 'trust') do not warn."""
    import warnings
    from modi_harness.policy.modes import normalize_mode

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        normalize_mode("auto")
        normalize_mode("preview")
        normalize_mode("trust")
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations == []
