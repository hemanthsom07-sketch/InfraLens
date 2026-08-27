"""Phase 7A: tests for app/services/analysis_store.py's AnalysisStore.

Tests a fake, injectable clock (a plain mutable counter) rather than
real time.sleep() for TTL behavior — deterministic and instant, the
same seam-injection pattern already used for
app.llm.providers.anthropic_provider's `transport`.
"""

import pytest

from app.graph.engine import GraphEngine
from app.models.ikm import InfrastructureModel
from app.services.analysis_store import AnalysisResult, AnalysisStore


def _result(analysis_id: str) -> AnalysisResult:
    """A minimal, valid AnalysisResult wrapping an empty graph — only
    analysis_id varies between calls, since the store treats the rest
    of AnalysisResult as opaque payload."""
    return AnalysisResult(
        analysis_id=analysis_id,
        repository="repo",
        total_files=0,
        languages=[],
        frameworks=[],
        infrastructure=[],
        infrastructure_model=InfrastructureModel(),
        graph_engine=GraphEngine.from_infrastructure_model(InfrastructureModel()),
        tree=[],
        created_at=0.0,
    )


class _FakeClock:
    """A controllable clock: starts at 0.0, advances only when told to."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- construction / validation -----------------------------------------------


def test_rejects_non_positive_max_size() -> None:
    with pytest.raises(ValueError):
        AnalysisStore(max_size=0)


def test_rejects_non_positive_ttl() -> None:
    with pytest.raises(ValueError):
        AnalysisStore(ttl_seconds=0)
    with pytest.raises(ValueError):
        AnalysisStore(ttl_seconds=-1)


# --- basic set/get -------------------------------------------------------------


def test_set_then_get_returns_the_same_result() -> None:
    store = AnalysisStore()
    result = _result("a")
    store.set("a", result)
    assert store.get("a") is result


def test_get_unknown_id_returns_none() -> None:
    store = AnalysisStore()
    assert store.get("never-set") is None


def test_len_reflects_number_of_live_entries() -> None:
    store = AnalysisStore()
    assert len(store) == 0
    store.set("a", _result("a"))
    store.set("b", _result("b"))
    assert len(store) == 2


def test_delete_removes_an_entry() -> None:
    store = AnalysisStore()
    store.set("a", _result("a"))
    store.delete("a")
    assert store.get("a") is None
    assert len(store) == 0


def test_delete_unknown_id_is_a_no_op() -> None:
    store = AnalysisStore()
    store.delete("never-set")  # must not raise


def test_clear_removes_everything() -> None:
    store = AnalysisStore()
    store.set("a", _result("a"))
    store.set("b", _result("b"))
    store.clear()
    assert len(store) == 0


# --- isolation between entries -------------------------------------------------


def test_multiple_analyses_remain_isolated() -> None:
    """Storing/reading one analysis_id must never return or affect
    another's result — the core isolation guarantee (Phase 7A spec
    item 8: one analysis must never accidentally reuse another
    repository's graph)."""
    store = AnalysisStore()
    result_a = _result("a")
    result_b = _result("b")
    store.set("a", result_a)
    store.set("b", result_b)

    assert store.get("a") is result_a
    assert store.get("b") is result_b
    assert store.get("a") is not store.get("b")


def test_overwriting_one_id_does_not_affect_another() -> None:
    store = AnalysisStore()
    result_a1 = _result("a")
    result_a2 = _result("a")
    result_b = _result("b")
    store.set("a", result_a1)
    store.set("b", result_b)
    store.set("a", result_a2)

    assert store.get("a") is result_a2
    assert store.get("b") is result_b


# --- LRU eviction (max_size) ---------------------------------------------------


def test_eviction_does_not_trigger_below_max_size() -> None:
    store = AnalysisStore(max_size=5)
    for i in range(5):
        store.set(str(i), _result(str(i)))
    assert len(store) == 5
    for i in range(5):
        assert store.get(str(i)) is not None


def test_exceeding_max_size_evicts_least_recently_used() -> None:
    store = AnalysisStore(max_size=2)
    store.set("a", _result("a"))
    store.set("b", _result("b"))
    store.get("a")  # touch "a" -> "b" is now the least-recently-used
    store.set("c", _result("c"))  # pushes size to 3 -> evicts "b"

    assert store.get("b") is None
    assert store.get("a") is not None
    assert store.get("c") is not None
    assert len(store) == 2


def test_eviction_order_follows_get_not_just_set() -> None:
    """A get() must count as a "use" for LRU purposes, not just set() —
    otherwise "least recently used" would really just be "oldest
    inserted", which is a different (weaker) policy than documented."""
    store = AnalysisStore(max_size=2)
    store.set("a", _result("a"))
    store.set("b", _result("b"))
    store.get("a")
    store.get("a")
    store.set("c", _result("c"))

    assert store.get("a") is not None  # repeatedly touched -> survives
    assert store.get("b") is None  # never touched again -> evicted


def test_repeated_overflow_keeps_store_bounded() -> None:
    store = AnalysisStore(max_size=3)
    for i in range(20):
        store.set(str(i), _result(str(i)))
    assert len(store) == 3
    # Only the most recent 3 ids should survive (no LRU touches in between).
    assert {store.get(str(i)) is not None for i in (17, 18, 19)} == {True}


# --- TTL expiry ------------------------------------------------------------------


def test_entry_is_retrievable_before_ttl_expires() -> None:
    clock = _FakeClock()
    store = AnalysisStore(ttl_seconds=10.0, clock=clock)
    store.set("a", _result("a"))
    clock.advance(9.0)
    assert store.get("a") is not None


def test_entry_expires_exactly_at_ttl_boundary() -> None:
    clock = _FakeClock()
    store = AnalysisStore(ttl_seconds=10.0, clock=clock)
    store.set("a", _result("a"))
    clock.advance(10.0)  # expires_at <= now -> expired
    assert store.get("a") is None


def test_entry_expires_after_ttl() -> None:
    clock = _FakeClock()
    store = AnalysisStore(ttl_seconds=5.0, clock=clock)
    store.set("a", _result("a"))
    clock.advance(100.0)
    assert store.get("a") is None


def test_expired_entry_is_actively_removed_on_lookup() -> None:
    """An expired entry found on get() is deleted on the spot, not just
    reported as a miss — len(store) must reflect that too."""
    clock = _FakeClock()
    store = AnalysisStore(ttl_seconds=5.0, clock=clock)
    store.set("a", _result("a"))
    assert len(store) == 1
    clock.advance(10.0)
    assert store.get("a") is None
    assert len(store) == 0


def test_setting_again_resets_the_ttl() -> None:
    clock = _FakeClock()
    store = AnalysisStore(ttl_seconds=5.0, clock=clock)
    store.set("a", _result("a"))
    clock.advance(4.0)
    store.set("a", _result("a"))  # resets expiry to now + 5.0 -> now=4, expires at 9
    clock.advance(4.0)  # now = 8, still before the reset expiry of 9
    assert store.get("a") is not None


def test_ttl_and_lru_are_independent_limits() -> None:
    """A fresh (non-expired) entry can still be evicted by LRU pressure,
    and an entry within max_size can still expire by TTL — the two
    limits are enforced independently, not as a combined score."""
    clock = _FakeClock()
    store = AnalysisStore(max_size=1, ttl_seconds=1000.0, clock=clock)
    store.set("a", _result("a"))
    store.set("b", _result("b"))  # "a" evicted by LRU despite being far from TTL expiry
    assert store.get("a") is None
    assert store.get("b") is not None
