"""Parses docker-compose.yml/.yaml into IKM 'service' components plus
depends_on relationships between them.

Compose allows a few fields to be written in more than one shape —
`environment` as a list of "KEY=VALUE" strings or a mapping, `depends_on`
as a list of service names or a mapping with health-check conditions.
Both shapes are normalized to the same metadata structure so downstream
consumers only need to handle one representation.
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
        service_ids: dict[str, str] = {}  # service name -> component id

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
                        "depends_on": self._parse_depends_on(service_def.get("depends_on")),
                    },
                )
            )

        # Second pass: depends_on can reference a service defined later in
        # the file, so relationships are wired up only once every service
        # has a component id.
        relationships: list[Relationship] = []
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