"""Phase 6A.2: Compose cross-file relationship resolution.

parse() only ever sees one file, so a depends_on referencing a service in
a *different* Compose file never resolved before this change. The new
resolve_references() (called from ikm_service, mirroring the Kubernetes/
Terraform pattern) fixes this, scoped to same-directory files only —
never the whole repo, to avoid a false positive between two unrelated
Compose projects elsewhere in a monorepo that happen to reuse a service
name like "db".
"""

from pathlib import Path

from app.parsers.compose_parser import ComposeParser, resolve_references
from tests.conftest import write


def _parse(tmp_repo: Path, filename: str, yaml_text: str):
    path = write(tmp_repo, filename, yaml_text)
    return ComposeParser().parse(path, tmp_repo).components


# --- positive: same directory, cross-file ------------------------------------


def test_depends_on_resolves_across_files_in_the_same_directory(tmp_repo: Path) -> None:
    base = _parse(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          backend:
            build: .
            depends_on:
              - database
        """,
    )
    override = _parse(
        tmp_repo,
        "docker-compose.override.yml",
        """
        services:
          database:
            image: postgres:15
        """,
    )
    relationships = resolve_references(base + override)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "depends_on"
    assert relationships[0].source == base[0].id
    assert relationships[0].target == override[0].id


# --- negative: different directories, same service name --------------------


def test_depends_on_does_not_resolve_across_different_directories(tmp_repo: Path) -> None:
    project_a = _parse(
        tmp_repo,
        "services/a/docker-compose.yml",
        """
        services:
          backend:
            build: .
            depends_on:
              - database
        """,
    )
    project_b = _parse(
        tmp_repo,
        "services/b/docker-compose.yml",
        """
        services:
          database:
            image: postgres:15
        """,
    )
    relationships = resolve_references(project_a + project_b)
    assert relationships == []


# --- regression: same-file behavior is unaffected (parse() untouched) ------


def test_same_file_depends_on_still_resolves_via_parse_alone(tmp_repo: Path) -> None:
    """parse() itself (Pass 2) must be completely untouched by this
    change — same-file depends_on keeps resolving without needing
    resolve_references() at all."""
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


def test_resolve_references_does_not_duplicate_same_file_relationship(tmp_repo: Path) -> None:
    """The cross-file pass must skip a depends_on name that's already a
    sibling in the same file — Pass 2 inside parse() already created
    that relationship; resolve_references() must not create a second,
    duplicate one for it."""
    components = _parse(
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
    relationships = resolve_references(components)
    assert relationships == []  # already resolved by Pass 2, not resolve_references()'s job


# --- negative: unresolvable name (no matching service anywhere) ------------


def test_depends_on_with_no_matching_service_anywhere_produces_no_relationship(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          backend:
            build: .
            depends_on:
              - does-not-exist
        """,
    )
    relationships = resolve_references(components)
    assert relationships == []


# --- multi-file, multi-service same directory (realistic override case) ---


def test_multiple_services_resolve_correctly_across_two_files_in_one_directory(tmp_repo: Path) -> None:
    base = _parse(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          backend:
            build: .
            depends_on:
              - database
              - cache
        """,
    )
    override = _parse(
        tmp_repo,
        "docker-compose.override.yml",
        """
        services:
          database:
            image: postgres:15
          cache:
            image: redis:7
        """,
    )
    relationships = resolve_references(base + override)
    assert len(relationships) == 2
    assert {r.relationship_type for r in relationships} == {"depends_on"}
    targets = {r.target for r in relationships}
    assert targets == {c.id for c in override}
