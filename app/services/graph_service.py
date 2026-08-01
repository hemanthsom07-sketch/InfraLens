"""Thin entry point for the Graph Engine.

Mirrors ikm_service.build_infrastructure_model()'s role exactly, one
layer up: this is the only file outside app/graph/ that api/v1/analyze.py
(or any future caller) should import from. api/ never reaches into
GraphBuilder, Graph, or NetworkX directly.
"""

from app.graph.engine import GraphEngine
from app.models.ikm import InfrastructureModel


def build_graph(model: InfrastructureModel) -> GraphEngine:
    """Build a queryable GraphEngine from an already-built InfrastructureModel."""
    return GraphEngine.from_infrastructure_model(model)
