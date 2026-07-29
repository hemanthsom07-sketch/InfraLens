"""Parses a Dockerfile into a single 'container' IKM component.

Handles the instructions Phase 3 asks for: FROM (base image), WORKDIR,
EXPOSE, ENV, COPY, ENTRYPOINT, and CMD. Both instruction forms are
supported wherever Docker itself supports two (ENV's legacy vs. key=value
form, CMD/ENTRYPOINT's exec vs. shell form). Line continuations (a
trailing backslash) are joined before parsing so a wrapped instruction is
still read as one instruction.
"""

import json
import re
from pathlib import Path

from app.models.ikm import Component, ComponentType, InfrastructureModel
from app.parsers.base import InfrastructureParser

_LINE_CONTINUATION_RE = re.compile(r"\\\s*\n")
_ENV_PAIR_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|\S+)')


class DockerfileParser(InfrastructureParser):
    """Parses a single Dockerfile into one 'container' component."""

    def parse(self, path: Path, repo_root: Path) -> InfrastructureModel:
        text = self._read_text(path)
        if text is None:
            return InfrastructureModel()

        joined = _LINE_CONTINUATION_RE.sub(" ", text)
        instructions = self._split_instructions(joined)

        base_images: list[str] = []
        workdir: str | None = None
        exposed_ports: list[int] = []
        env_vars: dict[str, str] = {}
        copy_instructions: list[dict[str, list[str] | str]] = []
        entrypoint: list[str] | str | None = None
        cmd: list[str] | str | None = None

        for directive, args in instructions:
            if directive == "FROM":
                tokens = args.split()
                if tokens:
                    base_images.append(tokens[0])  # "FROM <image> [AS <stage>]"
            elif directive == "WORKDIR":
                workdir = args.strip()
            elif directive == "EXPOSE":
                for token in args.split():
                    port = token.split("/")[0]  # drop optional /tcp or /udp
                    if port.isdigit():
                        exposed_ports.append(int(port))
            elif directive == "ENV":
                env_vars.update(self._parse_env(args))
            elif directive == "COPY":
                instruction = self._parse_copy(args)
                if instruction:
                    copy_instructions.append(instruction)
            elif directive == "ENTRYPOINT":
                entrypoint = self._parse_exec_or_shell(args)
            elif directive == "CMD":
                cmd = self._parse_exec_or_shell(args)

        relative_id = self._relative_id(path, repo_root)
        component = Component(
            id=f"docker:{relative_id}",
            name=path.name,
            type=ComponentType.CONTAINER,
            technology="docker",
            metadata={
                "source_file": relative_id,
                "base_image": base_images[-1] if base_images else None,
                "build_stages": base_images if len(base_images) > 1 else None,
                "workdir": workdir,
                "exposed_ports": exposed_ports,
                "environment": env_vars,
                "copy_instructions": copy_instructions,
                "entrypoint": entrypoint,
                "cmd": cmd,
            },
        )
        return InfrastructureModel(components=[component])

    @staticmethod
    def _split_instructions(text: str) -> list[tuple[str, str]]:
        """Split into (INSTRUCTION, rest-of-line) pairs, skipping comments and blank lines."""
        instructions: list[tuple[str, str]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            directive = parts[0].upper()
            args = parts[1] if len(parts) > 1 else ""
            instructions.append((directive, args))
        return instructions

    @staticmethod
    def _parse_env(args: str) -> dict[str, str]:
        if "=" in args:
            # Modern form: ENV KEY1=value1 KEY2="value 2"
            return {k: v.strip('"') for k, v in _ENV_PAIR_RE.findall(args)}
        # Legacy form: ENV <key> <value>
        parts = args.split(None, 1)
        if len(parts) == 2:
            return {parts[0]: parts[1].strip().strip('"')}
        return {}

    @staticmethod
    def _parse_copy(args: str) -> dict[str, list[str] | str] | None:
        # Drop flags like --from=builder or --chown=user:group.
        tokens = [t for t in args.split() if not t.startswith("--")]
        if len(tokens) < 2:
            return None
        return {"sources": tokens[:-1], "destination": tokens[-1]}

    @staticmethod
    def _parse_exec_or_shell(args: str) -> list[str] | str:
        args = args.strip()
        if args.startswith("["):
            try:
                return json.loads(args)
            except json.JSONDecodeError:
                pass
        return args