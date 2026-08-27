"""Shared test fixtures: writes small, realistic manifest files into a
temp directory and returns its path, so parser tests exercise the real
parse() entrypoint (read file -> parse) rather than hand-built Component
objects — closer to what actually happens against a cloned repository.
"""

import textwrap
from pathlib import Path

import pytest

from app.graph.engine import GraphEngine
from app.models.ikm import InfrastructureModel
from app.services.analysis_store import AnalysisResult


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """An empty temp directory standing in for a cloned repository root."""
    return tmp_path


def write(root: Path, relative_path: str, content: str) -> Path:
    """Write `content` to `root/relative_path`, creating parent dirs as
    needed. Dedents first: test fixtures are written as indented
    triple-quoted strings for readability, but YAML's `---` document
    separator is only recognized at column 0 — a uniform indentation
    left in place would silently break multi-document fixtures."""
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))
    return path


def make_analysis_result(
    graph_engine: GraphEngine,
    *,
    analysis_id: str = "test-analysis-id",
    repository: str = "example-repo",
    total_files: int = 0,
    languages: list[str] | None = None,
    frameworks: list[str] | None = None,
    infrastructure: list[str] | None = None,
    infrastructure_model: InfrastructureModel | None = None,
    tree: list | None = None,
    created_at: float = 0.0,
) -> AnalysisResult:
    """A fully-formed AnalysisResult wrapping `graph_engine`, with
    sensible empty/zero defaults for everything else (Phase 7A).

    Used by tests that want to exercise explanation_service /
    component_lookup_service / analysis_service against a hand-built
    GraphEngine without needing a real clone — the analysis-layer
    equivalent of this file's own `write()` helper for parser tests.
    """
    return AnalysisResult(
        analysis_id=analysis_id,
        repository=repository,
        total_files=total_files,
        languages=languages if languages is not None else [],
        frameworks=frameworks if frameworks is not None else [],
        infrastructure=infrastructure if infrastructure is not None else [],
        infrastructure_model=infrastructure_model if infrastructure_model is not None else InfrastructureModel(),
        graph_engine=graph_engine,
        tree=tree if tree is not None else [],
        created_at=created_at,
    )
