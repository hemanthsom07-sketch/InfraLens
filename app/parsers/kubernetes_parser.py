"""Parses Kubernetes YAML manifests into IKM components.

A single manifest file commonly holds several `---`-separated documents
(e.g. a Deployment and its Service defined together), so every document
is parsed, not just the first. Supports the six kinds Phase 3 asks for:
Deployment, Service, ConfigMap, Secret, Ingress, StatefulSet.

No relationships are generated here — e.g. linking a Service to the
Deployment it fronts requires matching the Service's label selector
against Pod template labels, which is cross-resource, Kubernetes-specific
reasoning that reads more like graph-building than "read this one file".
That's exactly the kind of thing requirement #7 keeps out of individual
parsers; it's a natural fit for the future Graph Engine phase, which will
already have every component this parser produces to work with.
"""

from pathlib import Path
from typing import Any

import yaml

from app.models.ikm import Component, ComponentType, InfrastructureModel
from app.parsers.base import InfrastructureParser

_SUPPORTED_KINDS = {"Deployment", "Service", "ConfigMap", "Secret", "Ingress", "StatefulSet"}


def _get(data: Any, *keys: str) -> Any:
    """Safely navigate nested dicts, returning None on any missing key
    or non-dict intermediate value."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


class KubernetesParser(InfrastructureParser):
    """Parses a single Kubernetes manifest file (one or more `---`-separated documents)."""

    def parse(self, path: Path, repo_root: Path) -> InfrastructureModel:
        text = self._read_text(path)
        if text is None:
            return InfrastructureModel()

        try:
            documents = list(yaml.safe_load_all(text))
        except yaml.YAMLError:
            return InfrastructureModel()

        relative_id = self._relative_id(path, repo_root)
        components: list[Component] = []
        for document in documents:
            if not isinstance(document, dict):
                continue
            component = self._parse_document(document, relative_id)
            if component is not None:
                components.append(component)

        return InfrastructureModel(components=components)

    @staticmethod
    def _parse_document(document: dict, relative_id: str) -> Component | None:
        kind = document.get("kind")
        if kind not in _SUPPORTED_KINDS:
            return None

        name = _get(document, "metadata", "name") or "unnamed"
        spec = document.get("spec") if isinstance(document.get("spec"), dict) else {}

        containers = KubernetesParser._extract_containers(spec)
        images = [c["image"] for c in containers if isinstance(c.get("image"), str)]
        ports = KubernetesParser._extract_ports(spec, containers)

        return Component(
            id=f"kubernetes:{relative_id}:{kind}:{name}",
            name=str(name),
            type=ComponentType.KUBERNETES_RESOURCE,
            technology="kubernetes",
            metadata={
                "source_file": relative_id,
                "kind": kind,
                "images": images,
                "ports": ports,
            },
        )

    @staticmethod
    def _extract_containers(spec: dict) -> list[dict]:
        # Deployment/StatefulSet nest containers under a pod template.
        # (Service/ConfigMap/Secret/Ingress have no containers, so this
        # correctly returns [] for them.)
        containers = _get(spec, "template", "spec", "containers")
        if isinstance(containers, list):
            return [c for c in containers if isinstance(c, dict)]
        return []

    @staticmethod
    def _extract_ports(spec: dict, containers: list[dict]) -> list[int]:
        ports: list[int] = []
        for container in containers:
            for port_entry in container.get("ports") or []:
                if isinstance(port_entry, dict) and isinstance(port_entry.get("containerPort"), int):
                    ports.append(port_entry["containerPort"])
        # Service manifests expose ports directly under spec.ports instead.
        for port_entry in spec.get("ports") or []:
            if isinstance(port_entry, dict) and isinstance(port_entry.get("port"), int):
                ports.append(port_entry["port"])
        return ports