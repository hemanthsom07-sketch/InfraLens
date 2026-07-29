"""Detects infrastructure & DevOps tooling by recognizing known filenames
and path patterns: Dockerfiles, Compose files, Terraform, Kubernetes
manifests, Helm charts, GitHub Actions workflows, and Nginx config.

Most of these are unambiguous from the filename alone. Kubernetes
manifests are the one exception — they're plain .yaml/.yml files, so
recognizing one means peeking at its content for the apiVersion/kind keys
every Kubernetes resource declares (checked with a couple of regexes
rather than a full YAML parse, since a dependency-free boolean check is
all "did I find a manifest" needs).
"""

import re
from pathlib import Path

_COMPOSE_FILENAMES = {"docker-compose.yml", "docker-compose.yaml"}

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


def _looks_like_kubernetes_manifest(path: Path) -> bool:
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
        name_lower = path.name.lower()

        if name_lower == "dockerfile" or name_lower.startswith("dockerfile."):
            found.add("Docker")
        elif name_lower in _COMPOSE_FILENAMES:
            found.add("Docker Compose")
        elif name_lower.endswith(".tf"):
            found.add("Terraform")
        elif name_lower == "chart.yaml":
            found.add("Helm")
        elif name_lower == "nginx.conf":
            found.add("Nginx")
        elif "/.github/workflows/" in path.as_posix():
            found.add("GitHub Actions")
        elif name_lower.endswith((".yaml", ".yml")) and _looks_like_kubernetes_manifest(path):
            found.add("Kubernetes")

    return sorted(found)