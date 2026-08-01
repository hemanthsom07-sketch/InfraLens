"""Shared test fixtures: writes small, realistic manifest files into a
temp directory and returns its path, so parser tests exercise the real
parse() entrypoint (read file -> parse) rather than hand-built Component
objects — closer to what actually happens against a cloned repository.
"""

import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """An empty temp directory standing in for a cloned repository root."""
    return tmp_path


def write(root: Path, relative_path: str, content: str) -> Path:
    """Write `content` to `root/relative_path`, creating parent dirs as
    needed. Dedents first: test fixtures are written as indented
    triple-quoted strings for readability, but YAML's `---` document
    separator is only recognized at column 0 — a uniform indentation
    left in place would silently break multi-document fixtures."""
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))
    return path
