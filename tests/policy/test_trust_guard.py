"""Trust mode must require MODI_ALLOW_TRUST=1 to be settable."""
from __future__ import annotations

import pytest


def test_trust_without_env_raises(monkeypatch) -> None:
    monkeypatch.delenv("MODI_ALLOW_TRUST", raising=False)
    from modi_harness.policy.modes import enforce_trust_guard
    with pytest.raises(RuntimeError, match="MODI_ALLOW_TRUST"):
        enforce_trust_guard("trust")


def test_trust_with_env_passes(monkeypatch) -> None:
    monkeypatch.setenv("MODI_ALLOW_TRUST", "1")
    from modi_harness.policy.modes import enforce_trust_guard
    enforce_trust_guard("trust")  # no raise


def test_other_modes_unaffected(monkeypatch) -> None:
    monkeypatch.delenv("MODI_ALLOW_TRUST", raising=False)
    from modi_harness.policy.modes import enforce_trust_guard
    enforce_trust_guard("auto")
    enforce_trust_guard("preview")


def test_trust_with_env_zero_raises(monkeypatch) -> None:
    """Only '1' counts as opt-in. '0' is the same as unset."""
    monkeypatch.setenv("MODI_ALLOW_TRUST", "0")
    from modi_harness.policy.modes import enforce_trust_guard
    with pytest.raises(RuntimeError):
        enforce_trust_guard("trust")
