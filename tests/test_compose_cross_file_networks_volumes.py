"""Phase 6C.6: Compose cross-file network/volume resolution.

Unlike depends_on (6A.2), a service's connects_to/mounts relationship to
its network/volume is built eagerly inside ComposeParser.parse()'s Pass
1, before cross-file visibility exists. So when docker-compose.yml and
docker-compose.override.yml (same directory) both reference a network
named "backend", each file's own parse() call creates its OWN separate
network component and its OWN relationship to it - two disconnected
nodes for what is really one logical shared network, not something a
resolve_references()-style "just add a relationship" pass can fix.
canonicalize_shared_resources() exists specifically for this: it merges
duplicate network/volume components (same directory, same name, same
type) into one canonical component and rewrites every relationship that
pointed at a duplicate to point at the canonical one instead.
"""

from pathlib import Path

from app.parsers.compose_parser import ComposeParser, canonicalize_shared_resources
from tests.conftest import write


def _parse(tmp_repo: Path, filename: str, yaml_text: str):
    path = write(tmp_repo, filename, yaml_text)
    return ComposeParser().parse(path, tmp_repo)


# --- same-directory network across files --------------------------------------


def test_same_directory_network_across_files_is_canonicalized(tmp_repo: Path) -> None:
    base = _parse(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          backend:
            build: .
            networks:
              - shared
        networks:
          shared: {}
        """,
    )
    override = _parse(
        tmp_repo,
        "docker-compose.override.yml",
        """
        services:
          api:
            build: .
            networks:
              - shared
        """,
    )
    components = base.components + override.components
    relationships = base.relationships + override.relationships

    canonical_components, canonical_relationships = canonicalize_shared_resources(components, relationships)

    networks = [c for c in canonical_components if c.type == "network" and c.name == "shared"]
    assert len(networks) == 1  # exactly ONE logical network, not two

    canonical_network_id = networks[0].id
    connects_to_edges = [r for r in canonical_relationships if r.relationship_type == "connects_to"]
    assert len(connects_to_edges) == 2  # both services still connect - to the SAME network
    assert {r.target for r in connects_to_edges} == {canonical_network_id}
    assert {r.source for r in connects_to_edges} == {
        c.id for c in components if c.type == "service"
    }


# --- same-directory volume across files ----------------------------------------


def test_same_directory_volume_across_files_is_canonicalized(tmp_repo: Path) -> None:
    base = _parse(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          db:
            image: postgres:15
            volumes:
              - db_data:/var/lib/postgresql/data
        volumes:
          db_data: {}
        """,
    )
    override = _parse(
        tmp_repo,
        "docker-compose.override.yml",
        """
        services:
          db-backup:
            image: postgres:15
            volumes:
              - db_data:/backup-source
        """,
    )
    components = base.components + override.components
    relationships = base.relationships + override.relationships

    canonical_components, canonical_relationships = canonicalize_shared_resources(components, relationships)

    volumes = [c for c in canonical_components if c.type == "volume" and c.name == "db_data"]
    assert len(volumes) == 1

    mounts_edges = [r for r in canonical_relationships if r.relationship_type == "mounts"]
    assert len(mounts_edges) == 2
    assert {r.target for r in mounts_edges} == {volumes[0].id}


# --- different-directory same-name does NOT resolve -------------------------


def test_different_directory_same_name_network_does_not_canonicalize(tmp_repo: Path) -> None:
    project_a = _parse(
        tmp_repo,
        "services/a/docker-compose.yml",
        """
        services:
          backend:
            build: .
            networks:
              - shared
        """,
    )
    project_b = _parse(
        tmp_repo,
        "services/b/docker-compose.yml",
        """
        services:
          frontend:
            build: .
            networks:
              - shared
        """,
    )
    components = project_a.components + project_b.components
    relationships = project_a.relationships + project_b.relationships

    canonical_components, canonical_relationships = canonicalize_shared_resources(components, relationships)

    # Nothing merged: still two separate network components, each in its
    # own directory, each with its own service still pointing at it.
    networks = [c for c in canonical_components if c.type == "network" and c.name == "shared"]
    assert len(networks) == 2
    assert canonical_relationships == relationships  # unchanged - no remap needed


def test_different_directory_same_name_volume_does_not_canonicalize(tmp_repo: Path) -> None:
    project_a = _parse(
        tmp_repo,
        "services/a/docker-compose.yml",
        "services:\n  db:\n    image: postgres:15\n    volumes:\n      - data:/var/lib/postgresql/data\n",
    )
    project_b = _parse(
        tmp_repo,
        "services/b/docker-compose.yml",
        "services:\n  cache:\n    image: redis:7\n    volumes:\n      - data:/data\n",
    )
    components = project_a.components + project_b.components
    relationships = project_a.relationships + project_b.relationships

    canonical_components, _ = canonicalize_shared_resources(components, relationships)
    volumes = [c for c in canonical_components if c.type == "volume" and c.name == "data"]
    assert len(volumes) == 2


# --- single-file regression ----------------------------------------------------


def test_single_file_network_unaffected_by_canonicalization(tmp_repo: Path) -> None:
    result = _parse(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          backend:
            build: .
            networks:
              - shared
          frontend:
            build: .
            networks:
              - shared
        networks:
          shared: {}
        """,
    )
    canonical_components, canonical_relationships = canonicalize_shared_resources(
        result.components, result.relationships
    )
    assert canonical_components == result.components
    assert canonical_relationships == result.relationships


# --- multiple services sharing the same network/volume ----------------------


def test_multiple_services_across_three_files_share_one_canonical_network(tmp_repo: Path) -> None:
    base = _parse(
        tmp_repo, "docker-compose.yml", "services:\n  a:\n    build: .\n    networks:\n      - shared\n"
    )
    override1 = _parse(
        tmp_repo, "docker-compose.override.yml", "services:\n  b:\n    build: .\n    networks:\n      - shared\n"
    )
    override2 = _parse(
        tmp_repo, "docker-compose.prod.yml", "services:\n  c:\n    build: .\n    networks:\n      - shared\n"
    )
    components = base.components + override1.components + override2.components
    relationships = base.relationships + override1.relationships + override2.relationships

    canonical_components, canonical_relationships = canonicalize_shared_resources(components, relationships)

    networks = [c for c in canonical_components if c.type == "network"]
    assert len(networks) == 1

    connects_to_edges = [r for r in canonical_relationships if r.relationship_type == "connects_to"]
    assert len(connects_to_edges) == 3
    assert {r.target for r in connects_to_edges} == {networks[0].id}


# --- no duplicate logical components after resolution / integrity ----------


def test_no_duplicate_components_remain_after_canonicalization(tmp_repo: Path) -> None:
    base = _parse(
        tmp_repo,
        "docker-compose.yml",
        "services:\n  a:\n    build: .\n    networks:\n      - shared\n    volumes:\n      - data:/data\n",
    )
    override = _parse(
        tmp_repo,
        "docker-compose.override.yml",
        "services:\n  b:\n    build: .\n    networks:\n      - shared\n    volumes:\n      - data:/data\n",
    )
    components = base.components + override.components
    relationships = base.relationships + override.relationships

    canonical_components, canonical_relationships = canonicalize_shared_resources(components, relationships)

    ids = {c.id for c in canonical_components}
    assert len(ids) == len(canonical_components)
    for relationship in canonical_relationships:
        assert relationship.source in ids
        assert relationship.target in ids


def test_canonicalization_is_a_no_op_when_nothing_shared(tmp_repo: Path) -> None:
    result = _parse(
        tmp_repo, "docker-compose.yml", "services:\n  solo:\n    build: .\n"
    )
    canonical_components, canonical_relationships = canonicalize_shared_resources(
        result.components, result.relationships
    )
    assert canonical_components == result.components
    assert canonical_relationships == result.relationships
