"""Recursively scans a cloned repository to build a directory tree,
count files, and detect programming languages by file extension."""

from collections import Counter
from pathlib import Path

from app.core.config import IGNORED_DIRECTORIES, LANGUAGE_EXTENSIONS
from app.models.schemas import TreeNode


def scan_repository(root: Path) -> tuple[int, list[str], list[TreeNode]]:
    """Walk `root` once and return (total_files, languages, tree).

    `languages` is ordered by number of files using that language, most
    common first. Directories in IGNORED_DIRECTORIES (see core.config) are
    skipped entirely — neither counted nor descended into.
    """
    language_counts: Counter[str] = Counter()
    file_count = 0

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
                language = LANGUAGE_EXTENSIONS.get(entry.suffix.lower())
                if language:
                    language_counts[language] += 1
                nodes.append(TreeNode(name=entry.name, type="file", children=None))

        return nodes

    tree = walk(root)
    languages = [language for language, _ in language_counts.most_common()]
    return file_count, languages, tree