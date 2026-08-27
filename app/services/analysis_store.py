"""AnalysisResult and AnalysisStore (Phase 7A).

AnalysisResult holds everything a completed analysis produced under one
analysis_id. It's a plain dataclass, not a Pydantic model — same
reasoning as scanner_service.ScanResult: it never crosses the API
boundary directly (graph_engine especially must never be serialized
as-is), so it doesn't need validation. app.services.analysis_service's
to_analyze_response() is the bridge that turns one into the existing
AnalyzeResponse wire shape, via GraphEngine.to_model() — the same bridge
GraphEngine already provides for every other consumer.

AnalysisStore is a bounded, in-process, single-instance cache — no
Redis, no database, no persistence across restarts, explicitly out of
scope for this phase. Two independent limits, both enforced on every
write:

- max_size: an LRU cap. A successful get() or set() marks that entry
  most-recently-used; the least-recently-used entry is evicted whenever
  a write would push the store over max_size.
- ttl_seconds: a per-entry expiry, checked lazily on lookup rather than
  swept by a background thread — simpler, and exactly as correct, since
  an expired-but-not-yet-evicted entry is functionally identical to a
  missing one for every caller (AnalysisStore.get() returns None either
  way, and an expired entry found on lookup is removed on the spot).

Thread-safety: FastAPI runs sync route handlers in a worker threadpool,
so concurrent get()/set() calls from different requests are a real
possibility even within a single process. A single lock around every
read/write keeps this store correct under that concurrency without
needing anything fancier than the stdlib.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from app.graph.engine import GraphEngine
from app.models.ikm import InfrastructureModel
from app.models.schemas import TreeNode


@dataclass
class AnalysisResult:
    """Everything one completed analysis produced, held together under
    one analysis_id.

    `graph_engine` is a fully-built GraphEngine (parsing, cross-file
    reference resolution, and inference already applied) — reusing it
    is the entire point of this phase: no consumer needs to re-clone,
    re-scan, re-parse, or rebuild the graph to use an AnalysisResult
    already in the store.
    """

    analysis_id: str
    repository: str
    total_files: int
    languages: list[str]
    frameworks: list[str]
    infrastructure: list[str]
    infrastructure_model: InfrastructureModel
    graph_engine: GraphEngine
    tree: list[TreeNode]
    created_at: float


@dataclass
class _Entry:
    result: AnalysisResult
    expires_at: float


class AnalysisStore:
    """Bounded LRU + TTL cache of AnalysisResult, keyed by analysis_id.

    `clock` is injectable (defaults to time.time) purely for
    deterministic testing of TTL behavior without real sleeps — the
    same seam-injection pattern already used elsewhere in this codebase
    (see app.llm.providers.anthropic_provider's injectable `transport`).
    """

    def __init__(
        self,
        max_size: int = 100,
        ttl_seconds: float = 1800.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be at least 1.")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive.")

        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def set(self, analysis_id: str, result: AnalysisResult) -> None:
        """Store `result` under `analysis_id`, resetting its TTL and
        marking it most-recently-used. If this write pushes the store
        over max_size, the least-recently-used entry (which may be the
        one just written, if max_size is 1 and something else was just
        touched) is evicted — eviction always targets whichever entry
        is oldest by LRU order after the write, never a special case.
        """
        expires_at = self._clock() + self._ttl_seconds
        with self._lock:
            self._entries[analysis_id] = _Entry(result=result, expires_at=expires_at)
            self._entries.move_to_end(analysis_id)
            while len(self._entries) > self._max_size:
                self._entries.popitem(last=False)

    def get(self, analysis_id: str) -> AnalysisResult | None:
        """The AnalysisResult stored under `analysis_id`, or None if
        it's never been set, has expired, or was evicted.

        A hit marks the entry most-recently-used (moves it to the back
        of the LRU order). An expired entry found here is actively
        removed from the store before returning None, rather than left
        to be evicted later by size pressure — so len(store) always
        reflects only genuinely live entries, not stale ones nobody
        has touched yet.
        """
        with self._lock:
            entry = self._entries.get(analysis_id)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                del self._entries[analysis_id]
                return None
            self._entries.move_to_end(analysis_id)
            return entry.result

    def delete(self, analysis_id: str) -> None:
        """Remove `analysis_id` if present. No-op if it isn't."""
        with self._lock:
            self._entries.pop(analysis_id, None)

    def clear(self) -> None:
        """Remove every entry. Test/reset use only — not used by any
        production code path in this phase."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        """The number of entries currently stored, including any that
        are expired but haven't been looked up (and therefore evicted)
        yet — this deliberately does NOT eagerly sweep expired entries,
        consistent with the lazy-expiry design; call get() on an id to
        force that check for that specific entry."""
        with self._lock:
            return len(self._entries)
