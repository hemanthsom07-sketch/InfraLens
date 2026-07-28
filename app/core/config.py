"""Shared constants used across the application.

Centralizing these here (instead of burying them in scanner_service.py)
makes it easy to extend in later phases — e.g. adding filename/pattern
recognition for Dockerfiles, Terraform, or Kubernetes manifests when the
infra-parsing phase is implemented.
"""

# Maps file extensions to human-readable language names.
# Matched case-insensitively (see scanner_service.py).
LANGUAGE_EXTENSIONS: dict[str, str] = {
    ".py": "Python",
    ".ipynb": "Jupyter Notebook",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
    ".dart": "Dart",
    ".lua": "Lua",
    ".pl": "Perl",
    ".r": "R",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "Sass",
    ".less": "Less",
    ".vue": "Vue",
    ".json": "JSON",
    ".xml": "XML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".tf": "Terraform",
}

# Directory names skipped entirely while scanning (never descended into,
# never counted). Only .git for now — it's version-control metadata, not
# repository content.
IGNORED_DIRECTORIES: set[str] = {".git"}