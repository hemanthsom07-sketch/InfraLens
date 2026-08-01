"""Recursively scans a cloned repository to build a directory tree,
count files, and detect programming languages by file extension."""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.core.config import IGNORED_DIRECTORIES, LANGUAGE_EXTENSIONS
from app.models.schemas import TreeNode


@dataclass
class ScanResult:
    """Everything a single walk of the repository collects.

    This is a plain dataclass, not a Pydantic model, on purpose: it never
    crosses the API boundary (AnalyzeResponse is what gets serialized to
    JSON), so it doesn't need validation — it's just a typed way to pass
    several results between services instead of an easy-to-misorder tuple.
    """

    total_files: int
    languages: list[str]
    tree: list[TreeNode]
    file_paths: list[Path]  # every file found, for framework/infra detection to filter


def scan_repository(root: Path) -> ScanResult:
    """Walk `root` once and collect everything downstream services need.

    `languages` is ordered by number of files using that language, most
    common first. Directories in IGNORED_DIRECTORIES (see core.config) are
    skipped entirely — neither counted, descended into, nor present in
    file_paths. `file_paths` holds every remaining file (any depth), which
    the Phase 2 framework/infrastructure detectors filter by filename.
    """
    language_counts: Counter[str] = Counter()
    file_count = 0
    file_paths: list[Path] = []

    def walk(directory: Path) -> list[TreeNode]:
        nonlocal file_count
        nodes: list[TreeNode] = []

        # Directories first, then files, both alphabetically
        # (case-insensitive) — matches how most file explorers display a tree.
        entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))

        for entry in entries:
            if entry.is_dir():
                if entry.name in IGNORED_DIRECTORIES:
                    continue
                nodes.append(TreeNode(name=entry.name, type="directory", children=walk(entry)))
            else:
                file_count += 1
                file_paths.append(entry)
                language = LANGUAGE_EXTENSIONS.get(entry.suffix.lower())
                if language:
                    language_counts[language] += 1
                nodes.append(TreeNode(name=entry.name, type="file", children=None))

        return nodes

    tree = walk(root)
    languages = [language for language, _ in language_counts.most_common()]
    return ScanResult(total_files=file_count, languages=languages, tree=tree, file_paths=file_paths)
