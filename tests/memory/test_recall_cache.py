from __future__ import annotations

from modi_harness.memory import RunRecallCache


def test_recall_cache_miss_then_hit() -> None:
    cache = RunRecallCache()
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return (["candidate"], ["selected"])

    assert cache.get_or_compute("run-1", compute) == (["candidate"], ["selected"])
    assert cache.get_or_compute("run-1", compute) == (["candidate"], ["selected"])
    assert calls["count"] == 1


def test_recall_cache_is_per_run() -> None:
    cache = RunRecallCache()
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return ([], [])

    cache.get_or_compute("run-1", compute)
    cache.get_or_compute("run-2", compute)

    assert calls["count"] == 2


def test_recall_cache_invalidate_recomputes() -> None:
    cache = RunRecallCache()
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return ([calls["count"]], [])

    assert cache.get_or_compute("run-1", compute) == ([1], [])
    cache.invalidate("run-1")
    assert cache.get_or_compute("run-1", compute) == ([2], [])


def test_recall_cache_invalidate_missing_run_is_harmless() -> None:
    cache = RunRecallCache()
    cache.invalidate("missing")
