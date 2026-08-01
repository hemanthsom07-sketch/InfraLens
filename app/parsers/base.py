"""Common interface for all infrastructure parsers."""

from abc import ABC, abstractmethod
from pathlib import Path

from app.models.ikm import InfrastructureModel


class InfrastructureParser(ABC):
    """Base class every concrete parser (Dockerfile, Compose, Terraform,
    Kubernetes, ...) implements.

    A parser reads exactly one file and returns the components and
    relationships it found in it — nothing else. It doesn't know about
    any other technology, and it doesn't know or care whether its output
    ends up in a graph, an AI explanation, a security scan, or a cost
    report; it only populates the Infrastructure Knowledge Model.
    """

    @abstractmethod
    def parse(self, path: Path, repo_root: Path) -> InfrastructureModel:
        """Parse a single file and return the components/relationships it
        contributes.

        `repo_root` is the root of the scanned repository, used to build
        clean, repo-relative identifiers (e.g. "backend/Dockerfile")
        instead of leaking the temporary clone's absolute, randomly-named
        path. Must never raise for a malformed file — return an empty
        InfrastructureModel() instead, the same tolerant-by-default
        approach the Phase 2 detectors use for unreadable manifests.
        """
        raise NotImplementedError

    @staticmethod
    def _read_text(path: Path) -> str | None:
        """Read a file as text, or None if it can't be read/decoded."""
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

    @staticmethod
    def _relative_id(path: Path, repo_root: Path) -> str:
        """A stable, human-readable, repo-relative path string for use in
        component ids — e.g. "backend/Dockerfile" rather than the
        temporary clone's full absolute path."""
        try:
            return path.relative_to(repo_root).as_posix()
        except ValueError:
            return path.name
