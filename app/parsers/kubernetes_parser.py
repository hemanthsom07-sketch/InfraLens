"""Parses Kubernetes YAML manifests into IKM components, plus the
references between them: Deployment/StatefulSet -> ConfigMap/Secret, and
Ingress -> Service.

A single manifest file commonly holds several `---`-separated documents
(e.g. a Deployment and its Service defined together), so every document
is parsed, not just the first. Supports the six kinds Phase 3 asks for:
Deployment, Service, ConfigMap, Secret, Ingress, StatefulSet.

Service -> Deployment/StatefulSet correlation (via label selectors) is
still *not* generated here — that one genuinely does need cross-resource,
graph-level reasoning (matching a selector against pod labels across
potentially many workloads) and stays exactly where the approved Phase 4
architecture put it: app/graph/inference.py, as an inferred graph edge,
not an IKM relationship.

ConfigMap/Secret/Service references are different: a Deployment's own
spec directly *names* the ConfigMap/Secret it wants (no matching/inference
required, just a name lookup), so these are extracted as real IKM
relationships instead — via resolve_references(), a module-level function
mirroring terraform_parser.py's function of the same name and same
purpose: resolving references that may point at a component declared in
a *different* file, which is why it can't happen inside parse() itself.
"""

from pathlib import Path
from typing import Any

import yaml

from app.models.ikm import Component, ComponentType, InfrastructureModel, Relationship, RelationshipType
from app.parsers.base import InfrastructureParser

_SUPPORTED_KINDS = {"Deployment", "Service", "ConfigMap", "Secret", "Ingress", "StatefulSet"}
_WORKLOAD_KINDS = {"Deployment", "StatefulSet"}

# Keys Kubernetes uses, in various places (env, envFrom, volumes), to
# reference a ConfigMap or a Secret by name — searched for anywhere in a
# workload's spec, so this catches both the env-var and volume-mount
# forms of referencing one in a single mechanism.
_CONFIGMAP_REF_KEYS = frozenset({"configMapRef", "configMapKeyRef", "configMap"})
_SECRET_REF_KEYS = frozenset({"secretRef", "secretKeyRef", "secret"})


def _get(data: Any, *keys: str) -> Any:
    """Safely navigate nested dicts, returning None on any missing key
    or non-dict intermediate value."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _find_named_references(data: Any, ref_keys: frozenset[str]) -> set[str]:
    """Recursively search `data` for a `{ref_key: {"name": ..., ...}}`
    shape, for any key in `ref_keys`, collecting every referenced name
    found — e.g. {"configMapRef": {"name": "app-config"}} anywhere in a
    pod spec, regardless of whether it came from an env var or a mounted
    volume. Checks "secretName" as a fallback: Kubernetes is inconsistent
    here — every reference form uses "name" *except* a Secret volume
    (`secret: {secretName: ...}`), which uses "secretName" instead."""
    found: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if key in ref_keys and isinstance(value, dict):
                ref_name = value.get("name") or value.get("secretName")
                if isinstance(ref_name, str):
                    found.add(ref_name)
            found |= _find_named_references(value, ref_keys)
    elif isinstance(data, list):
        for item in data:
            found |= _find_named_references(item, ref_keys)
    return found


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

        metadata: dict[str, Any] = {
            "source_file": relative_id,
            "kind": kind,
            "images": images,
            "ports": ports,
        }

        # Captured only when present, same convention as every other
        # optional field below — a missing namespace is NOT defaulted to
        # "default" here. A manifest with no namespace field carries no
        # actual evidence about which namespace it lands in (that's
        # commonly decided externally: `kubectl apply -n`, a Kustomize
        # overlay, a Helm --namespace flag), so coercing it to the
        # literal string "default" would assert something the manifest
        # doesn't say. See resolve_references()/graph/inference.py for
        # how this "unspecified" state participates in matching.
        namespace = _get(document, "metadata", "namespace")
        if isinstance(namespace, str) and namespace:
            metadata["namespace"] = namespace

        # Captured here (and nowhere else) so downstream reference
        # resolution has exactly the data it needs, without this parser
        # needing to know how that resolution works — see module docstring.
        if kind == "Service":
            selector = _get(spec, "selector")
            if isinstance(selector, dict):
                metadata["selector"] = selector
        elif kind in _WORKLOAD_KINDS:
            pod_labels = _get(spec, "template", "metadata", "labels")
            if isinstance(pod_labels, dict):
                metadata["pod_labels"] = pod_labels
            configmap_refs = _find_named_references(spec, _CONFIGMAP_REF_KEYS)
            if configmap_refs:
                metadata["configmap_refs"] = sorted(configmap_refs)
            secret_refs = _find_named_references(spec, _SECRET_REF_KEYS)
            if secret_refs:
                metadata["secret_refs"] = sorted(secret_refs)
        elif kind == "Ingress":
            service_refs = _find_named_references(spec, frozenset({"service"}))
            legacy_service_name = _get(spec, "backend", "serviceName")  # older Ingress API version
            if isinstance(legacy_service_name, str):
                service_refs.add(legacy_service_name)
            if service_refs:
                metadata["service_refs"] = sorted(service_refs)

        return Component(
            id=f"kubernetes:{relative_id}:{kind}:{name}",
            name=str(name),
            type=ComponentType.KUBERNETES_RESOURCE,
            technology="kubernetes",
            metadata=metadata,
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


def resolve_references(components: list[Component]) -> list[Relationship]:
    """Cross-check every Kubernetes component's configmap_refs/secret_refs/
    service_refs against Kubernetes components actually declared —
    possibly in a different manifest file — producing a `uses` (for
    ConfigMap/Secret) or `connects_to` (for Ingress -> Service)
    relationship per real match. Called once from
    ikm_service.build_infrastructure_model(), after every file has
    already been parsed.

    NAMESPACE SCOPING: the lookup key includes each component's own
    namespace (component.metadata.get("namespace"), None if absent), and
    a reference is always resolved against the REFERENCING component's
    own namespace — never a separately-specified one. This matches real
    Kubernetes semantics: a Pod can only reference a ConfigMap/Secret in
    its own namespace, and an Ingress backend can only reference a
    Service in its own namespace; cross-namespace references of these
    kinds aren't something core Kubernetes supports. "Unspecified"
    namespace is its own equivalence class — it matches another
    unspecified namespace, but never an explicit one (see
    KubernetesParser._parse_document's namespace-capture comment for why
    a missing namespace is never coerced to "default").
    """
    k8s_components = [c for c in components if c.technology == "kubernetes"]
    by_namespace_name_and_kind: dict[tuple[str | None, str, str], Component] = {
        (c.metadata.get("namespace"), c.name, c.metadata.get("kind")): c for c in k8s_components
    }

    relationships: list[Relationship] = []
    for component in k8s_components:
        kind = component.metadata.get("kind")
        namespace = component.metadata.get("namespace")
        if kind in _WORKLOAD_KINDS:
            for ref_name in component.metadata.get("configmap_refs", []):
                target = by_namespace_name_and_kind.get((namespace, ref_name, "ConfigMap"))
                if target is not None:
                    relationships.append(
                        Relationship(source=component.id, target=target.id, relationship_type=RelationshipType.USES)
                    )
            for ref_name in component.metadata.get("secret_refs", []):
                target = by_namespace_name_and_kind.get((namespace, ref_name, "Secret"))
                if target is not None:
                    relationships.append(
                        Relationship(source=component.id, target=target.id, relationship_type=RelationshipType.USES)
                    )
        elif kind == "Ingress":
            for ref_name in component.metadata.get("service_refs", []):
                target = by_namespace_name_and_kind.get((namespace, ref_name, "Service"))
                if target is not None:
                    relationships.append(
                        Relationship(
                            source=component.id, target=target.id, relationship_type=RelationshipType.CONNECTS_TO
                        )
                    )
    return relationships
