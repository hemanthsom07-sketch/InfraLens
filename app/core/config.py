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
# never counted). Version-control metadata and common vendored/build
# directories — none of these represent a project's own source, and a
# committed node_modules in particular would otherwise flood total_files
# and slow down Phase 2's dependency-file scanning with irrelevant nested
# package.json files.
IGNORED_DIRECTORIES: set[str] = {".git", "node_modules", "__pycache__", ".venv", "venv"}
