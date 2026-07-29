"""Detects application frameworks by inspecting known dependency and
configuration files (requirements.txt, pyproject.toml, package.json,
pom.xml, go.mod, Cargo.toml) found anywhere in the scanned repository.

Each _scan_* function reads exactly one manifest file and returns the set
of framework names it recognizes inside it. Parsing is deliberately
tolerant: an unreadable or malformed manifest just contributes nothing,
rather than failing the whole analysis — a broken file shouldn't stop the
rest of the repo from being reported on.
"""

import json
import re
import tomllib
from pathlib import Path

# Matches the package name at the start of a PEP 508 requirement line —
# e.g. "fastapi==0.115.0", "uvicorn[standard]>=0.32", "Django ; python_version>='3.10'"
# all yield just the leading identifier, stopping at the first version/extras/marker.
_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")

_PYTHON_FRAMEWORKS: dict[str, str] = {
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
}

_NODE_FRAMEWORKS_EXACT: dict[str, str] = {
    "react": "React",
    "next": "Next.js",
    "express": "Express",
    "vue": "Vue",
}
# Angular and NestJS ship as scoped packages (@angular/core, @nestjs/common,
# ...) with no single unscoped "angular"/"nestjs" dependency to match on.
_NODE_FRAMEWORKS_PREFIX: dict[str, str] = {
    "@angular/": "Angular",
    "@nestjs/": "NestJS",
}

# Not in the original spec, which named specific frameworks for every other
# ecosystem but only listed the manifest file for Go and Rust. Added for
# parity using each ecosystem's most common web frameworks — remove or
# extend these two dicts freely, nothing else depends on them.
_GO_FRAMEWORKS: dict[str, str] = {
    "gin-gonic/gin": "Gin",
    "labstack/echo": "Echo",
    "gofiber/fiber": "Fiber",
}
_RUST_FRAMEWORKS: dict[str, str] = {
    "actix-web": "Actix Web",
    "rocket": "Rocket",
    "axum": "Axum",
}


def _read_text(path: Path) -> str | None:
    """Read a file as text, or None if it can't be read/decoded.
    Dependency files are never critical enough to fail the whole analysis."""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _scan_requirements_txt(path: Path) -> set[str]:
    text = _read_text(path)
    if text is None:
        return set()

    found: set[str] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()  # drop comments
        match = _REQUIREMENT_NAME_RE.match(line)
        if match:
            name = match.group(1).lower()
            if name in _PYTHON_FRAMEWORKS:
                found.add(_PYTHON_FRAMEWORKS[name])
    return found


def _scan_pyproject_toml(path: Path) -> set[str]:
    text = _read_text(path)
    if text is None:
        return set()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return set()

    found: set[str] = set()

    # PEP 621 standard: [project] dependencies = ["fastapi>=0.100", ...]
    for requirement in data.get("project", {}).get("dependencies", []):
        match = _REQUIREMENT_NAME_RE.match(requirement)
        if match and match.group(1).lower() in _PYTHON_FRAMEWORKS:
            found.add(_PYTHON_FRAMEWORKS[match.group(1).lower()])

    # Legacy Poetry: [tool.poetry.dependencies] fastapi = "^0.100"
    poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name in poetry_deps:
        if name.lower() in _PYTHON_FRAMEWORKS:
            found.add(_PYTHON_FRAMEWORKS[name.lower()])

    return found


def _scan_package_json(path: Path) -> set[str]:
    text = _read_text(path)
    if text is None:
        return set()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return set()

    all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}

    found: set[str] = set()
    for raw_name in all_deps:
        name = raw_name.lower()
        if name in _NODE_FRAMEWORKS_EXACT:
            found.add(_NODE_FRAMEWORKS_EXACT[name])
        for prefix, framework in _NODE_FRAMEWORKS_PREFIX.items():
            if name.startswith(prefix):
                found.add(framework)
    return found


def _scan_pom_xml(path: Path) -> set[str]:
    text = _read_text(path)
    if text is None:
        return set()
    # A full XML parse would only ever check for this same artifactId
    # substring, so a direct text search gets the same answer more simply.
    return {"Spring Boot"} if "spring-boot" in text.lower() else set()


def _scan_go_mod(path: Path) -> set[str]:
    text = _read_text(path)
    if text is None:
        return set()
    text_lower = text.lower()
    return {name for module_path, name in _GO_FRAMEWORKS.items() if module_path in text_lower}


def _scan_cargo_toml(path: Path) -> set[str]:
    text = _read_text(path)
    if text is None:
        return set()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return set()
    deps = data.get("dependencies", {})
    return {_RUST_FRAMEWORKS[name.lower()] for name in deps if name.lower() in _RUST_FRAMEWORKS}


# Dispatch table: manifest filename (lowercase) -> function that scans it.
# Adding a new ecosystem later is one dict entry plus one function.
_MANIFEST_SCANNERS = {
    "requirements.txt": _scan_requirements_txt,
    "pyproject.toml": _scan_pyproject_toml,
    "package.json": _scan_package_json,
    "pom.xml": _scan_pom_xml,
    "go.mod": _scan_go_mod,
    "cargo.toml": _scan_cargo_toml,
}


def detect_frameworks(file_paths: list[Path]) -> list[str]:
    """Inspect known manifest files among `file_paths` (as produced by
    scan_repository) and return detected framework names, alphabetically
    sorted with duplicates removed.

    Manifests are matched by filename at any depth, so a monorepo with
    e.g. backend/pyproject.toml and frontend/package.json is handled
    correctly, not just manifests at the repository root.
    """
    found: set[str] = set()
    for path in file_paths:
        scanner = _MANIFEST_SCANNERS.get(path.name.lower())
        if scanner:
            found |= scanner(path)
    return sorted(found)