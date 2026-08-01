"""Parses docker-compose.yml/.yaml into IKM components plus relationships
between them: one 'service' component per service, plus 'network' and
'volume' components for anything services share, wired up with
depends_on / connects_to / mounts relationships respectively.

Compose allows a few fields to be written in more than one shape —
`environment` as a list of "KEY=VALUE" strings or a mapping, `depends_on`
as a list of service names or a mapping with health-check conditions,
`networks` the same way. All are normalized to the same metadata
structure so downstream consumers only need to handle one representation.
"""

from pathlib import Path
from typing import Any

import yaml

from app.models.ikm import (
    Component,
    ComponentType,
    InfrastructureModel,
    Relationship,
    RelationshipType,
)
from app.parsers.base import InfrastructureParser


class ComposeParser(InfrastructureParser):
    """Parses a single docker-compose.yml/.yaml file."""

    def parse(self, path: Path, repo_root: Path) -> InfrastructureModel:
        text = self._read_text(path)
        if text is None:
            return InfrastructureModel()

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            return InfrastructureModel()

        if not isinstance(data, dict):
            return InfrastructureModel()
        services = data.get("services")
        if not isinstance(services, dict):
            return InfrastructureModel()

        relative_id = self._relative_id(path, repo_root)
        components: list[Component] = []
        relationships: list[Relationship] = []
        service_ids: dict[str, str] = {}  # service name -> component id
        network_ids: dict[str, str] = {}  # network name -> component id, filled in as discovered
        volume_ids: dict[str, str] = {}  # named volume -> component id, filled in as discovered

        # Pass 1: one component per service, and — as they're discovered —
        # one component per distinct named network/volume any service
        # references, plus the connects_to/mounts relationships tying
        # services to them. This can all happen in a single pass because,
        # unlike depends_on below, a network or volume doesn't need to
        # already have a component before a service can reference it.
        for service_name, service_def in services.items():
            if not isinstance(service_def, dict):
                service_def = {}
            component_id = f"compose:{relative_id}:{service_name}"
            service_ids[service_name] = component_id
            components.append(
                Component(
                    id=component_id,
                    name=str(service_name),
                    type=ComponentType.SERVICE,
                    technology="docker-compose",
                    metadata={
                        "source_file": relative_id,
                        "image": service_def.get("image"),
                        "build_context": self._parse_build(service_def.get("build")),
                        "ports": self._as_str_list(service_def.get("ports")),
                        "environment": self._parse_environment(service_def.get("environment")),
                        "volumes": self._as_str_list(service_def.get("volumes")),
                        "networks": self._parse_networks(service_def.get("networks")),
                        "depends_on": self._parse_depends_on(service_def.get("depends_on")),
                    },
                )
            )

            for network_name in self._parse_networks(service_def.get("networks")):
                if network_name not in network_ids:
                    network_ids[network_name] = f"compose:{relative_id}:network:{network_name}"
                    components.append(
                        Component(
                            id=network_ids[network_name],
                            name=network_name,
                            type=ComponentType.NETWORK,
                            technology="docker-compose",
                            metadata={"source_file": relative_id},
                        )
                    )
                relationships.append(
                    Relationship(
                        source=component_id,
                        target=network_ids[network_name],
                        relationship_type=RelationshipType.CONNECTS_TO,
                    )
                )

            for volume_entry in service_def.get("volumes", []) or []:
                named_volume = self._extract_named_volume(volume_entry)
                if named_volume is None:
                    continue  # a bind mount or anonymous volume, not a named one to share
                if named_volume not in volume_ids:
                    volume_ids[named_volume] = f"compose:{relative_id}:volume:{named_volume}"
                    components.append(
                        Component(
                            id=volume_ids[named_volume],
                            name=named_volume,
                            type=ComponentType.VOLUME,
                            technology="docker-compose",
                            metadata={"source_file": relative_id},
                        )
                    )
                relationships.append(
                    Relationship(
                        source=component_id,
                        target=volume_ids[named_volume],
                        relationship_type=RelationshipType.MOUNTS,
                    )
                )

        # Pass 2: depends_on relationships. Needs its own pass — a service
        # can depend on one defined *later* in the file, so every
        # service's component id must already exist first.
        for service_name, service_def in services.items():
            if not isinstance(service_def, dict):
                continue
            for dependency in self._parse_depends_on(service_def.get("depends_on")):
                if dependency in service_ids:
                    relationships.append(
                        Relationship(
                            source=service_ids[service_name],
                            target=service_ids[dependency],
                            relationship_type=RelationshipType.DEPENDS_ON,
                        )
                    )

        return InfrastructureModel(components=components, relationships=relationships)

    @staticmethod
    def _parse_build(build: Any) -> str | None:
        if isinstance(build, str):
            return build
        if isinstance(build, dict):
            context = build.get("context")
            return str(context) if context is not None else None
        return None

    @staticmethod
    def _as_str_list(value: Any) -> list[str]:
        """Normalize a Compose list-or-single-value field to a list of strings."""
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]

    @staticmethod
    def _parse_environment(value: Any) -> dict[str, str]:
        """Compose allows environment as a list of "KEY=VALUE" strings or
        a mapping of KEY: VALUE — normalize both to a dict."""
        if isinstance(value, dict):
            return {str(k): ("" if v is None else str(v)) for k, v in value.items()}
        if isinstance(value, list):
            env: dict[str, str] = {}
            for item in value:
                if isinstance(item, str) and "=" in item:
                    key, _, val = item.partition("=")
                    env[key] = val
            return env
        return {}

    @staticmethod
    def _parse_depends_on(value: Any) -> list[str]:
        """Compose allows depends_on as a list of service names or a
        mapping of service_name: {condition: ...} — normalize both to a
        list of service names."""
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, dict):
            return [str(k) for k in value.keys()]
        return []

    @staticmethod
    def _parse_networks(value: Any) -> list[str]:
        """Compose allows per-service networks as a list of names or a
        mapping of name: {aliases: [...], ...} — same normalization
        pattern as _parse_depends_on."""
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, dict):
            return [str(k) for k in value.keys()]
        return []

    @staticmethod
    def _extract_named_volume(volume_entry: str) -> str | None:
        """From a short-syntax volume string like "db_data:/var/lib/data"
        or "db_data:/path:ro", return the named volume ("db_data") — or
        None if this is a bind mount (starts with "." or "/", e.g.
        "./config:/etc/app") or an anonymous volume (no ":" at all).
        Long-syntax (dict-form) volume entries aren't handled here — they
        never reach this function since _as_str_list stringifies them,
        which won't look like "name:path" and correctly won't match."""
        if ":" not in volume_entry:
            return None
        source = volume_entry.split(":", 1)[0]
        if source.startswith((".", "/", "~", "$")):
            return None
        return source
