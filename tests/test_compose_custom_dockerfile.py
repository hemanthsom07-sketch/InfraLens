"""Phase 6D.2: Compose custom `build.dockerfile:` resolution.

Before this change, app.graph.inference's Compose->Dockerfile rule
always assumed the literal filename "Dockerfile", so a service using a
custom-named Dockerfile (build: {context: ./api, dockerfile: Dockerfile.prod})
never correlated to it. `dockerfile:` is explicit, first-class Compose
syntax, so honoring it is no less certain than the existing default case.

No dedicated test previously existed for this inference rule at all
(only indirect coverage via the full pipeline tests) - this file closes
that gap too, not just the new custom-filename behavior.
"""

from pathlib import Path

from app.graph.inference import infer_compose_dockerfile_edges
from app.parsers.compose_parser import ComposeParser
from app.parsers.docker_parser import DockerfileParser
from tests.conftest import write


def _parse_compose(tmp_repo: Path, filename: str, yaml_text: str):
    path = write(tmp_repo, filename, yaml_text)
    return ComposeParser().parse(path, tmp_repo).components


def _parse_dockerfile(tmp_repo: Path, filename: str, dockerfile_text: str):
    path = write(tmp_repo, filename, dockerfile_text)
    return DockerfileParser().parse(path, tmp_repo).components


# --- regression: default filename (existing behavior) -----------------------


def test_default_dockerfile_name_still_resolves(tmp_repo: Path) -> None:
    compose = _parse_compose(tmp_repo, "docker-compose.yml", "services:\n  backend:\n    build: ./backend\n")
    dockerfile = _parse_dockerfile(tmp_repo, "backend/Dockerfile", "FROM python:3.12\n")
    edges = infer_compose_dockerfile_edges(compose + dockerfile)
    assert len(edges) == 1
    assert edges[0].edge_type == "uses"
    assert edges[0].metadata["confidence"] == "high"


def test_build_dockerfile_field_absent_for_short_form_build(tmp_repo: Path) -> None:
    compose = _parse_compose(tmp_repo, "docker-compose.yml", "services:\n  backend:\n    build: .\n")
    assert compose[0].metadata["build_dockerfile"] is None


# --- positive: custom dockerfile name --------------------------------------


def test_custom_dockerfile_name_captured(tmp_repo: Path) -> None:
    compose = _parse_compose(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          backend:
            build:
              context: ./backend
              dockerfile: Dockerfile.prod
        """,
    )
    assert compose[0].metadata["build_dockerfile"] == "Dockerfile.prod"


def test_custom_dockerfile_name_resolves_correctly(tmp_repo: Path) -> None:
    compose = _parse_compose(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          backend:
            build:
              context: ./backend
              dockerfile: Dockerfile.prod
        """,
    )
    dockerfile = _parse_dockerfile(tmp_repo, "backend/Dockerfile.prod", "FROM python:3.12\n")
    edges = infer_compose_dockerfile_edges(compose + dockerfile)
    assert len(edges) == 1
    assert edges[0].edge_type == "uses"
    assert edges[0].metadata["confidence"] == "high"
    assert edges[0].metadata["basis"] == "build context path match"


def test_custom_dockerfile_name_does_not_match_default_dockerfile(tmp_repo: Path) -> None:
    """A service explicitly naming Dockerfile.prod must not accidentally
    match a plain "Dockerfile" sitting in the same directory - the
    custom name is honored exactly, not treated as an alternative."""
    compose = _parse_compose(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          backend:
            build:
              context: ./backend
              dockerfile: Dockerfile.prod
        """,
    )
    dockerfile = _parse_dockerfile(tmp_repo, "backend/Dockerfile", "FROM python:3.12\n")
    edges = infer_compose_dockerfile_edges(compose + dockerfile)
    assert edges == []


def test_default_dockerfile_does_not_match_when_custom_named_dockerfile_exists_elsewhere(tmp_repo: Path) -> None:
    """The flip side: a service using the default filename must not
    match a Dockerfile with some OTHER custom name in the same
    directory."""
    compose = _parse_compose(tmp_repo, "docker-compose.yml", "services:\n  backend:\n    build: ./backend\n")
    dockerfile = _parse_dockerfile(tmp_repo, "backend/Dockerfile.dev", "FROM python:3.12\n")
    edges = infer_compose_dockerfile_edges(compose + dockerfile)
    assert edges == []


# --- boundary: missing target -------------------------------------------------


def test_custom_dockerfile_missing_target_produces_no_edge(tmp_repo: Path) -> None:
    compose = _parse_compose(
        tmp_repo,
        "docker-compose.yml",
        "services:\n  backend:\n    build:\n      context: ./backend\n      dockerfile: Dockerfile.prod\n",
    )
    edges = infer_compose_dockerfile_edges(compose)
    assert edges == []
