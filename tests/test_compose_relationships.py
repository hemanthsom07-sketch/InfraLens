"""Docker Compose: depends_on, shared networks, shared volumes."""

from pathlib import Path

from app.parsers.compose_parser import ComposeParser
from tests.conftest import write


def test_compose_produces_depends_on_relationship(tmp_repo: Path) -> None:
    path = write(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          backend:
            build: .
            depends_on:
              - database
          database:
            image: postgres:15
        """,
    )
    result = ComposeParser().parse(path, tmp_repo)

    assert len(result.relationships) == 1
    assert result.relationships[0].relationship_type == "depends_on"


def test_compose_produces_shared_network_relationships(tmp_repo: Path) -> None:
    path = write(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          web:
            image: myapp
            networks: [backend-net]
          worker:
            image: myworker
            networks: [backend-net]
        """,
    )
    result = ComposeParser().parse(path, tmp_repo)

    network_components = [c for c in result.components if c.type == "network"]
    assert len(network_components) == 1, "the shared network should be one component, not duplicated"

    connects_to = [r for r in result.relationships if r.relationship_type == "connects_to"]
    assert len(connects_to) == 2, "both services should connect to the shared network"
    assert {r.target for r in connects_to} == {network_components[0].id}


def test_compose_produces_shared_volume_relationships(tmp_repo: Path) -> None:
    path = write(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          app:
            image: myapp
            volumes:
              - shared-data:/data
              - ./config:/etc/config
        """,
    )
    result = ComposeParser().parse(path, tmp_repo)

    volume_components = [c for c in result.components if c.type == "volume"]
    assert len(volume_components) == 1, "only the named volume should become a component, not the bind mount"
    assert volume_components[0].name == "shared-data"

    mounts = [r for r in result.relationships if r.relationship_type == "mounts"]
    assert len(mounts) == 1


def test_compose_single_service_has_no_depends_on(tmp_repo: Path) -> None:
    """Not every valid compose file has relationships to find — a
    single-service file legitimately produces zero depends_on edges.
    This isn't the bug that was reported; it's correct behavior."""
    path = write(tmp_repo, "docker-compose.yml", "services:\n  web:\n    build: .\n")
    result = ComposeParser().parse(path, tmp_repo)
    assert result.relationships == []
