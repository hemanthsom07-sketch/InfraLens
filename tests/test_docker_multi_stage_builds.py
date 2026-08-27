"""Phase 6D.1: Docker multi-stage build metadata.

`FROM <image> AS <stage>` and `COPY --from=<stage>` are explicit,
unambiguous Dockerfile syntax. Captured as metadata only
(build_stage_names, per-instruction from_stage, and the aggregate
copy_from_stages) - the 1-file-1-component invariant stays intact, no
new graph relationship is invented since there's no second component to
connect to.
"""

from pathlib import Path

from app.parsers.docker_parser import DockerfileParser
from tests.conftest import write


def _parse(tmp_repo: Path, filename: str, dockerfile_text: str):
    path = write(tmp_repo, filename, dockerfile_text)
    return DockerfileParser().parse(path, tmp_repo).components[0]


# --- stage name capture ---------------------------------------------------------


def test_single_stage_dockerfile_has_no_stage_names(tmp_repo: Path) -> None:
    component = _parse(tmp_repo, "Dockerfile", 'FROM python:3.12\nCMD ["python", "app.py"]\n')
    assert "build_stage_names" not in component.metadata


def test_multi_stage_dockerfile_captures_stage_names(tmp_repo: Path) -> None:
    component = _parse(
        tmp_repo,
        "Dockerfile",
        """
        FROM golang:1.21 AS builder
        WORKDIR /src
        RUN go build -o app .
        FROM alpine
        COPY --from=builder /src/app /usr/local/bin/app
        """,
    )
    assert component.metadata["build_stage_names"] == ["builder"]
    assert component.metadata["build_stages"] == ["golang:1.21", "alpine"]


def test_multiple_named_stages_captured_in_order(tmp_repo: Path) -> None:
    component = _parse(
        tmp_repo,
        "Dockerfile",
        """
        FROM golang:1.21 AS builder
        FROM node:20 AS frontend
        FROM alpine
        COPY --from=builder /src/app /usr/local/bin/app
        COPY --from=frontend /src/dist /var/www
        """,
    )
    assert component.metadata["build_stage_names"] == ["builder", "frontend"]


def test_lowercase_as_keyword_recognized(tmp_repo: Path) -> None:
    component = _parse(tmp_repo, "Dockerfile", "FROM golang:1.21 as builder\nFROM alpine\n")
    assert component.metadata["build_stage_names"] == ["builder"]


def test_unnamed_intermediate_stage_produces_no_stage_name_entry(tmp_repo: Path) -> None:
    """A FROM with no AS clause contributes nothing to build_stage_names
    - it's still counted in build_stages (the existing image list), just
    not addressable by name."""
    component = _parse(tmp_repo, "Dockerfile", "FROM golang:1.21\nFROM alpine\n")
    assert "build_stage_names" not in component.metadata
    assert component.metadata["build_stages"] == ["golang:1.21", "alpine"]


# --- COPY --from capture ------------------------------------------------------


def test_copy_from_named_stage_captured_per_instruction(tmp_repo: Path) -> None:
    component = _parse(
        tmp_repo,
        "Dockerfile",
        """
        FROM golang:1.21 AS builder
        FROM alpine
        COPY --from=builder /src/app /usr/local/bin/app
        """,
    )
    copy_instructions = component.metadata["copy_instructions"]
    assert len(copy_instructions) == 1
    assert copy_instructions[0]["from_stage"] == "builder"
    assert copy_instructions[0]["sources"] == ["/src/app"]
    assert copy_instructions[0]["destination"] == "/usr/local/bin/app"


def test_copy_from_stages_aggregate_captured(tmp_repo: Path) -> None:
    component = _parse(
        tmp_repo,
        "Dockerfile",
        """
        FROM golang:1.21 AS builder
        FROM node:20 AS frontend
        FROM alpine
        COPY --from=builder /src/app /usr/local/bin/app
        COPY --from=frontend /src/dist /var/www
        """,
    )
    assert component.metadata["copy_from_stages"] == ["builder", "frontend"]


def test_copy_from_numeric_index_captured_as_is(tmp_repo: Path) -> None:
    """--from=0 (positional index, not a name) is captured verbatim, not
    resolved to a stage name - that resolution is out of this phase's
    metadata-only scope."""
    component = _parse(
        tmp_repo, "Dockerfile", "FROM golang:1.21\nFROM alpine\nCOPY --from=0 /src/app /usr/local/bin/app\n"
    )
    assert component.metadata["copy_from_stages"] == ["0"]


def test_copy_without_from_flag_has_no_from_stage_key(tmp_repo: Path) -> None:
    component = _parse(tmp_repo, "Dockerfile", "FROM python:3.12\nCOPY . /app\n")
    copy_instructions = component.metadata["copy_instructions"]
    assert "from_stage" not in copy_instructions[0]
    assert "copy_from_stages" not in component.metadata


def test_copy_from_alongside_other_flags(tmp_repo: Path) -> None:
    """--chown and --from can appear together - only --from is captured,
    --chown is still dropped exactly as before."""
    component = _parse(
        tmp_repo,
        "Dockerfile",
        "FROM golang:1.21 AS builder\nFROM alpine\nCOPY --chown=app:app --from=builder /src/app /usr/local/bin/app\n",
    )
    copy_instructions = component.metadata["copy_instructions"]
    assert copy_instructions[0]["from_stage"] == "builder"
    assert copy_instructions[0]["sources"] == ["/src/app"]


# --- regression: 1-file-1-component invariant, no new relationships --------


def test_multi_stage_dockerfile_still_produces_exactly_one_component(tmp_repo: Path) -> None:
    path = write(
        tmp_repo,
        "Dockerfile",
        """
        FROM golang:1.21 AS builder
        FROM alpine
        COPY --from=builder /src/app /usr/local/bin/app
        """,
    )
    result = DockerfileParser().parse(path, tmp_repo)
    assert len(result.components) == 1
    assert result.relationships == []


def test_existing_base_image_and_workdir_fields_unaffected(tmp_repo: Path) -> None:
    component = _parse(
        tmp_repo,
        "Dockerfile",
        """
        FROM python:3.12
        WORKDIR /app
        EXPOSE 8080
        CMD ["python", "app.py"]
        """,
    )
    assert component.metadata["base_image"] == "python:3.12"
    assert component.metadata["build_stages"] is None
    assert component.metadata["workdir"] == "/app"
    assert component.metadata["exposed_ports"] == [8080]
