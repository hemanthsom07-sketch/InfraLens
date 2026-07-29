"""Builds the Infrastructure Knowledge Model for a scanned repository.

Dispatches each file to the parser for its technology — reusing the same
is_*() predicates infrastructure_service.py uses for the Phase 2
`infrastructure` field, so the two can never disagree about what counts
as e.g. a Dockerfile — and merges every parser's output into one
InfrastructureModel.
"""

from pathlib import Path

from app.models.ikm import InfrastructureModel
from app.parsers.base import InfrastructureParser
from app.parsers.compose_parser import ComposeParser
from app.parsers.docker_parser import DockerfileParser
from app.parsers.kubernetes_parser import KubernetesParser
from app.parsers.terraform_parser import TerraformParser
from app.services.infrastructure_service import (
    is_compose_file,
    is_dockerfile,
    is_kubernetes_manifest,
    is_terraform_file,
)

# Parsers are stateless (parse() takes everything it needs as arguments
# and stores nothing on self), so one shared instance per technology is
# enough — no need to construct a fresh one for every file.
_DOCKER_PARSER = DockerfileParser()
_COMPOSE_PARSER = ComposeParser()
_TERRAFORM_PARSER = TerraformParser()
_KUBERNETES_PARSER = KubernetesParser()


def _parser_for(path: Path) -> InfrastructureParser | None:
    """Pick the parser for `path` based on the same rules
    detect_infrastructure() uses, or None if no Phase 3 parser handles it
    (Helm/Nginx/GitHub Actions are still reported in the plain
    `infrastructure` list, just not yet parsed into the IKM)."""
    if is_dockerfile(path):
        return _DOCKER_PARSER
    if is_compose_file(path):
        return _COMPOSE_PARSER
    if is_terraform_file(path):
        return _TERRAFORM_PARSER
    if is_kubernetes_manifest(path):
        return _KUBERNETES_PARSER
    return None


def build_infrastructure_model(file_paths: list[Path], repo_root: Path) -> InfrastructureModel:
    """Parse every recognized infrastructure file among `file_paths` and
    merge the results into a single InfrastructureModel."""
    components = []
    relationships = []

    for path in file_paths:
        parser = _parser_for(path)
        if parser is None:
            continue

        try:
            result = parser.parse(path, repo_root)
        except Exception:
            # A parser bug or a genuinely unusual file should never take
            # down the whole analysis — consistent with how Phase 2
            # treats a malformed dependency file: that one file just
            # contributes nothing.
            continue

        components.extend(result.components)
        relationships.extend(result.relationships)

    return InfrastructureModel(components=components, relationships=relationships)