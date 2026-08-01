"""Detects infrastructure & DevOps tooling by recognizing known filenames
and path patterns: Dockerfiles, Compose files, Terraform, Kubernetes
manifests, Helm charts, GitHub Actions workflows, and Nginx config.

Most of these are unambiguous from the filename alone. Kubernetes
manifests are the one exception — they're plain .yaml/.yml files, so
recognizing one means peeking at its content for the apiVersion/kind keys
every Kubernetes resource declares (checked with a couple of regexes
rather than a full YAML parse, since a dependency-free boolean check is
all "did I find a manifest" needs).

The is_*() predicates below are also imported directly by
services/ikm_service.py (Phase 3) to decide which parser a file goes to.
Pulling them out into named functions — rather than duplicating the same
filename rules in two places — means detect_infrastructure() and the
Infrastructure Knowledge Model can never disagree about what counts as,
say, a Dockerfile. detect_infrastructure()'s own behavior is unchanged
from Phase 2; this is a pure extract-method refactor, verified by rerunning
the exact Phase 2 test fixture.
"""

import re
from pathlib import Path

_COMPOSE_FILENAMES = {
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
}

# Every Kubernetes resource manifest declares both of these as top-level
# keys — the standard, reliable signal for "this YAML file is a manifest".
_K8S_MARKERS = (
    re.compile(r"^\s*apiVersion:\s*\S", re.MULTILINE),
    re.compile(r"^\s*kind:\s*\S", re.MULTILINE),
)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def is_dockerfile(path: Path) -> bool:
    name_lower = path.name.lower()
    return name_lower == "dockerfile" or name_lower.startswith("dockerfile.")


def is_compose_file(path: Path) -> bool:
    return path.name.lower() in _COMPOSE_FILENAMES


def is_terraform_file(path: Path) -> bool:
    return path.name.lower().endswith(".tf")


def is_helm_chart(path: Path) -> bool:
    return path.name.lower() == "chart.yaml"


def is_nginx_config(path: Path) -> bool:
    return path.name.lower() == "nginx.conf"


def is_github_actions_workflow(path: Path) -> bool:
    return "/.github/workflows/" in path.as_posix()


def is_kubernetes_manifest(path: Path) -> bool:
    """True if this is a .yaml/.yml file whose content declares both
    apiVersion and kind — the standard signal for a Kubernetes manifest."""
    if not path.name.lower().endswith((".yaml", ".yml")):
        return False
    text = _read_text(path)
    if text is None:
        return False
    return all(marker.search(text) for marker in _K8S_MARKERS)


def detect_infrastructure(file_paths: list[Path]) -> list[str]:
    """Inspect `file_paths` (as produced by scan_repository) and return
    detected infrastructure/DevOps technologies, alphabetically sorted
    with duplicates removed.
    """
    found: set[str] = set()

    for path in file_paths:
        if is_dockerfile(path):
            found.add("Docker")
        elif is_compose_file(path):
            found.add("Docker Compose")
        elif is_terraform_file(path):
            found.add("Terraform")
        elif is_helm_chart(path):
            found.add("Helm")
        elif is_nginx_config(path):
            found.add("Nginx")
        elif is_github_actions_workflow(path):
            found.add("GitHub Actions")
        elif is_kubernetes_manifest(path):
            found.add("Kubernetes")

    return sorted(found)
