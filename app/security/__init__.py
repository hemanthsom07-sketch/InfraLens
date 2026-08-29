"""Deterministic, evidence-backed security rules (Phase 7E).

Every rule here operates ONLY on the already-parsed InfrastructureModel
(app.models.ikm) — the exact same structured data the explanation layer
already reads. No rule invents a fact the parsers don't actually capture,
no rule calls an LLM, and no rule reaches into GraphEngine or networkx:
these are pure, deterministic checks over Component.metadata.
"""
