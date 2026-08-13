"""AI Explanation Engine (Phase 5).

Built entirely on top of app.graph.engine.GraphEngine — the explanation
subsystem never imports networkx and never reaches into the Graph
Engine's internals. GraphEngine remains the source of truth for every
infrastructure relationship; this package only reads what it already
exposes and repackages that into explanation-ready shapes.

Stage 5B (evidence.py) — structured, deterministic evidence gathering.
Later stages (fallback wording, LLM-assisted generation, API wiring) are
not yet implemented.
"""
