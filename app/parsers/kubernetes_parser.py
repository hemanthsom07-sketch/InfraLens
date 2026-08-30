"""Parses Kubernetes YAML manifests into IKM components, plus the
references between them: workload -> ConfigMap/Secret/PersistentVolumeClaim/
ServiceAccount, Ingress -> Service, HorizontalPodAutoscaler -> its scale
target, and Namespace -> everything declared in it.

A single manifest file commonly holds several `---`-separated documents
(e.g. a Deployment and its Service defined together), so every document
is parsed, not just the first. Supported kinds (Phase 3's original six,
Phase 6C's coverage expansion, plus Phase 6D.4's Namespace): Deployment,
Service, ConfigMap, Secret, Ingress, StatefulSet, DaemonSet, Job,
CronJob, PersistentVolumeClaim, ServiceAccount, HorizontalPodAutoscaler,
Namespace.

Service -> workload correlation (via label selectors) is still *not*
generated here — that one genuinely does need cross-resource, graph-level
reasoning (matching a selector against pod labels across potentially many
workloads) and stays exactly where the approved Phase 4 architecture put
it: app/graph/inference.py, as an inferred graph edge, not an IKM
relationship. HorizontalPodAutoscaler -> workload is different: an HPA's
own spec directly *names* its scale target (kind + name, no matching
required), so — like ConfigMap/Secret/PVC/ServiceAccount/Service
references — it's a real IKM relationship via resolve_references(),
below.

WORKLOAD KINDS AND POD-TEMPLATE-BEARING KINDS (Phase 6C): every kind that
owns Pods via a pod template — Deployment, StatefulSet, DaemonSet, Job,
and CronJob (whose pod template is nested one level deeper, inside
spec.jobTemplate.spec) — shares identical container/label/reference
extraction, so _WORKLOAD_KINDS covers all five uniformly (see
_parse_document's CronJob remapping comment). Kubernetes' Service->Pod
routing mechanism is agnostic to which controller owns a pod, so all five
are also valid Service-selector inference targets in app/graph/inference.py
— this isn't a heuristic relaxation, it's the same "exactly how Kubernetes
itself decides" justification rule 1 already had, just correctly extended
to every pod-owning kind rather than arbitrarily stopping at two.
HorizontalPodAutoscaler is intentionally narrower: real Kubernetes HPA
targets only implement the "scale" subresource (Deployment, StatefulSet,
ReplicaSet in real clusters) — DaemonSet has no replica count to scale,
and Job/CronJob aren't valid scale targets in the Kubernetes API at all.
_HPA_VALID_TARGET_KINDS is deliberately {"Deployment", "StatefulSet"} —
narrower than _WORKLOAD_KINDS — to avoid ever inventing a target
relationship the real Kubernetes API would itself reject.

ConfigMap/Secret/PVC/ServiceAccount/Service references are different from
Service-selector correlation: a workload's own spec directly *names* what
it wants (no matching/inference required, just a name lookup), so these
are extracted as real IKM relationships instead — via resolve_references(),
a module-level function mirroring terraform_parser.py's function of the
same name and same purpose: resolving references that may point at a
component declared in a *different* file, which is why it can't happen
inside parse() itself.

NAMESPACE COMPONENTS AND CONTAINS RELATIONSHIPS (Phase 6D.4): a
`kind: Namespace` manifest becomes its own component, and every OTHER
Kubernetes component whose `metadata["namespace"]` names a declared
Namespace gets a `Namespace --contains--> component` relationship —
`metadata.namespace: X` matching a declared `kind: Namespace,
metadata.name: X` is a direct, explicit field match, exactly as certain
as every other name-lookup relationship in this file.

IMPORTANT — DELIBERATE SCOPE-RULE EXCEPTION: unlike every other
relationship this parser or app/graph/inference.py produces, Namespace
containment is NOT scoped to the same directory/file. A Kubernetes
namespace is a cluster-wide resource — it isn't tied to a repository
directory at all, and a real repo commonly declares its Namespace object
in one file while the resources that live in it are scattered across many
others, in many different directories. Directory-scoping this
relationship would be *wrong*, not conservative — it would silently
under-report containment for the single most common real-world layout.
This is the one place in the whole codebase where directory-scoping does
NOT apply, and it's a deliberate, evidence-backed exception, not an
inconsistency — do not "fix" this into directory-scoping by mistaken
consistency with 6A.7/6A.2/6B/6C's directory-scoped relationships, which
all exist to prevent a different, real failure mode.
"""

from pathlib import Path
from typing import Any

import yaml

from app.models.ikm import Component, ComponentType, InfrastructureModel, Relationship, RelationshipType
from app.parsers.base import InfrastructureParser

_SUPPORTED_KINDS = {
    "Deployment",
    "Service",
    "ConfigMap",
    "Secret",
    "Ingress",
    "StatefulSet",
    "DaemonSet",
    "Job",
    "CronJob",
    "PersistentVolumeClaim",
    "ServiceAccount",
    "HorizontalPodAutoscaler",
    "Namespace",
}
# Every kind that owns Pods via a pod template. See module docstring for
# why this is 5 kinds, not 2, and why that's not a scope relaxation.
_WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
# Real Kubernetes HPA targets only ever implement the "scale" subresource
# (Deployment, StatefulSet, ReplicaSet on a real cluster). DaemonSet has
# no replica count; Job/CronJob aren't scale targets at all. ReplicaSet
# isn't a kind this parser recognizes (it's normally owned by a
# Deployment, rarely authored directly), so this project's valid set is
# {"Deployment", "StatefulSet"} — narrower than _WORKLOAD_KINDS on
# purpose, not an oversight.
_HPA_VALID_TARGET_KINDS = {"Deployment", "StatefulSet"}

# Keys Kubernetes uses, in various places (env, envFrom, volumes), to
# reference a ConfigMap or a Secret by name — searched for anywhere in a
# workload's spec, so this catches both the env-var and volume-mount
# forms of referencing one in a single mechanism.
_CONFIGMAP_REF_KEYS = frozenset({"configMapRef", "configMapKeyRef", "configMap"})
_SECRET_REF_KEYS = frozenset({"secretRef", "secretKeyRef", "secret"})
# A volume's persistentVolumeClaim entry: {"persistentVolumeClaim": {"claimName": "..."}}.
_PVC_REF_KEYS = frozenset({"persistentVolumeClaim"})


def _get(data: Any, *keys: str) -> Any:
    """Safely navigate nested dicts, returning None on any missing key
    or non-dict intermediate value."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _effective_privileged(container: dict) -> bool | None:
    """Kubernetes' `privileged` field exists ONLY in a container's own
    SecurityContext — PodSecurityContext (the pod-level securityContext)
    has no `privileged` field at all in the real Kubernetes API. So
    unlike runAsNonRoot below, there is no pod-level value to ever fall
    back to for this one; it's purely per-container, by API design."""
    security_context = container.get("securityContext")
    if isinstance(security_context, dict):
        value = security_context.get("privileged")
        if isinstance(value, bool):
            return value
    return None


def _effective_run_as_non_root(container: dict, pod_security_context: dict | None) -> bool | None:
    """`runAsNonRoot` exists in BOTH a Pod's own securityContext
    (PodSecurityContext) and each container's own securityContext
    (SecurityContext). Real Kubernetes precedence, from the API's own
    documented behavior: an explicit container-level value always wins
    for that container; the pod-level value applies only when the
    container itself doesn't set one; if neither sets it, the effective
    value genuinely can't be determined from the manifest alone."""
    security_context = container.get("securityContext")
    if isinstance(security_context, dict):
        value = security_context.get("runAsNonRoot")
        if isinstance(value, bool):
            return value
    if isinstance(pod_security_context, dict):
        value = pod_security_context.get("runAsNonRoot")
        if isinstance(value, bool):
            return value
    return None


def _collapse_container_security_flags(effective_values: list[bool | None], *, unsafe_value: bool) -> bool | None:
    """Collapse one boolean security flag's effective per-container
    values (see _effective_privileged/_effective_run_as_non_root above)
    into a single workload-level fact, worst-case-wins:

    - If ANY container's effective value equals `unsafe_value`, the
      workload-level result is `unsafe_value` — a pod is only as safe as
      its least safe container, so one privileged (or one
      allowed-to-run-as-root) container makes the whole workload that
      way, regardless of the others.
    - Else, if EVERY container explicitly states the safe value (none
      are None), the workload-level result is the safe value — every
      container was unambiguously confirmed.
    - Otherwise (no container states the unsafe value, but at least one
      states neither value — i.e. is None) the result is None: some
      containers never say either way, so this can't be confirmed safe,
      but there's also no evidence it's unsafe — absence of evidence
      stays absence of evidence, never coerced into a guess in either
      direction. An empty container list (a malformed or contentless
      manifest) also produces None, not a vacuous "safe" claim.
    """
    if any(value == unsafe_value for value in effective_values):
        return unsafe_value
    if effective_values and all(value == (not unsafe_value) for value in effective_values):
        return not unsafe_value
    return None


def _find_named_references(data: Any, ref_keys: frozenset[str]) -> set[str]:
    """Recursively search `data` for a `{ref_key: {"name": ..., ...}}`
    shape, for any key in `ref_keys`, collecting every referenced name
    found — e.g. {"configMapRef": {"name": "app-config"}} anywhere in a
    pod spec, regardless of whether it came from an env var or a mounted
    volume. Checks "secretName"/"claimName" as fallbacks: Kubernetes is
    inconsistent here — most reference forms use "name", except a Secret
    volume (`secret: {secretName: ...}`) and a PVC volume
    (`persistentVolumeClaim: {claimName: ...}`), which use their own key
    instead."""
    found: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if key in ref_keys and isinstance(value, dict):
                ref_name = value.get("name") or value.get("secretName") or value.get("claimName")
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

        # CronJob nests its pod-owning spec one level deeper, inside
        # jobTemplate.spec — which is itself shaped exactly like an
        # ordinary Job's spec (its own .template.metadata.labels,
        # .template.spec.containers, .template.spec.serviceAccountName,
        # all at the same relative depth as every other workload kind).
        # Extracting it once here means every extraction below reuses
        # the exact same code path Job already uses, rather than
        # duplicating container/label/ref logic for CronJob specifically.
        workload_spec = spec
        if kind == "CronJob":
            job_spec = _get(spec, "jobTemplate", "spec")
            workload_spec = job_spec if isinstance(job_spec, dict) else {}

        containers = KubernetesParser._extract_containers(workload_spec)
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
            # Phase 7F: real Kubernetes defaults an omitted Service
            # `type` to "ClusterIP" server-side — matched here exactly,
            # not an invented default, since "unspecified" and
            # "explicitly ClusterIP" are the same real-world fact for a
            # Service (unlike privileged/run_as_non_root below, where
            # "never set" is kept genuinely distinct from an explicit
            # value).
            service_type = _get(spec, "type")
            metadata["service_type"] = service_type if isinstance(service_type, str) and service_type else "ClusterIP"
        elif kind in _WORKLOAD_KINDS:
            pod_labels = _get(workload_spec, "template", "metadata", "labels")
            if isinstance(pod_labels, dict):
                metadata["pod_labels"] = pod_labels
            configmap_refs = _find_named_references(workload_spec, _CONFIGMAP_REF_KEYS)
            if configmap_refs:
                metadata["configmap_refs"] = sorted(configmap_refs)
            secret_refs = _find_named_references(workload_spec, _SECRET_REF_KEYS)
            if secret_refs:
                metadata["secret_refs"] = sorted(secret_refs)
            pvc_refs = _find_named_references(workload_spec, _PVC_REF_KEYS)
            if pvc_refs:
                metadata["pvc_refs"] = sorted(pvc_refs)
            service_account_name = _get(workload_spec, "template", "spec", "serviceAccountName")
            if isinstance(service_account_name, str) and service_account_name:
                metadata["service_account_ref"] = service_account_name
            # Phase 7F: privileged/run_as_non_root — see
            # _effective_privileged/_effective_run_as_non_root/
            # _collapse_container_security_flags above for the exact
            # per-container precedence rule and the worst-case-wins
            # aggregation across containers. Genuinely three-valued
            # (True/False/None) — the key is OMITTED (not set to None)
            # when unknown, the same "omit an optional key rather than a
            # placeholder" convention every other optional field in this
            # file already follows, so `metadata.get("privileged")`
            # naturally returns None either way.
            pod_security_context = _get(workload_spec, "template", "spec", "securityContext")
            pod_security_context = pod_security_context if isinstance(pod_security_context, dict) else None
            privileged = _collapse_container_security_flags(
                [_effective_privileged(c) for c in containers], unsafe_value=True
            )
            if privileged is not None:
                metadata["privileged"] = privileged
            run_as_non_root = _collapse_container_security_flags(
                [_effective_run_as_non_root(c, pod_security_context) for c in containers], unsafe_value=False
            )
            if run_as_non_root is not None:
                metadata["run_as_non_root"] = run_as_non_root
        elif kind == "Secret":
            # Phase 7F: a Secret manifest has NO `spec` field at all in
            # the real Kubernetes API — `type`/`data`/`stringData` are
            # all document-ROOT fields, siblings of apiVersion/kind/
            # metadata, not nested under spec (unlike every workload
            # kind above). Read from `document`, not `spec`, to match
            # real manifest structure.
            #
            # SECURITY INVARIANT: only WHETHER data/stringData is
            # present and non-empty is captured (a bool) — never the
            # actual keys or values. No Secret material of any kind ever
            # enters Component.metadata.
            secret_type = _get(document, "type")
            if isinstance(secret_type, str) and secret_type:
                metadata["secret_type"] = secret_type
            data = document.get("data")
            string_data = document.get("stringData")
            metadata["has_data"] = bool(isinstance(data, dict) and data) or bool(
                isinstance(string_data, dict) and string_data
            )
        elif kind == "Ingress":
            service_refs = _find_named_references(spec, frozenset({"service"}))
            legacy_service_name = _get(spec, "backend", "serviceName")  # older Ingress API version
            if isinstance(legacy_service_name, str):
                service_refs.add(legacy_service_name)
            if service_refs:
                metadata["service_refs"] = sorted(service_refs)
            # Phase 7F: a definite yes/no fact (unlike privileged/
            # run_as_non_root) — always set, never omitted. `spec.tls`
            # present but empty ([]) is treated the same as absent: no
            # TLS is actually configured either way.
            tls = _get(spec, "tls")
            metadata["has_tls"] = isinstance(tls, list) and len(tls) > 0
        elif kind == "HorizontalPodAutoscaler":
            target_kind = _get(spec, "scaleTargetRef", "kind")
            target_name = _get(spec, "scaleTargetRef", "name")
            # Captured as-is, whatever kind is named — validity against
            # _HPA_VALID_TARGET_KINDS is checked at resolution time in
            # resolve_references(), not here, so a malformed or
            # unsupported scaleTargetRef is still visible in metadata
            # for inspection even when it can't be resolved.
            if isinstance(target_kind, str) and isinstance(target_name, str):
                metadata["scale_target_kind"] = target_kind
                metadata["scale_target_name"] = target_name
        # Namespace itself needs no extra extraction beyond what's
        # already captured above (source_file/kind/images=[]/ports=[]) —
        # it's a plain component; see resolve_references() for how other
        # components' namespace field resolves against it.

        return Component(
            id=f"kubernetes:{relative_id}:{kind}:{name}",
            name=str(name),
            type=ComponentType.KUBERNETES_RESOURCE,
            technology="kubernetes",
            metadata=metadata,
        )

    @staticmethod
    def _extract_containers(workload_spec: dict) -> list[dict]:
        # Every _WORKLOAD_KINDS member nests containers under a pod
        # template at this same relative path (CronJob's own jobTemplate.spec
        # remapping — see _parse_document — makes this true for it too).
        # Non-workload kinds (Service/ConfigMap/Secret/Ingress/PVC/
        # ServiceAccount/HPA) have no containers, so this correctly
        # returns [] for them.
        containers = _get(workload_spec, "template", "spec", "containers")
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
    pvc_refs/service_account_ref/service_refs/scale_target against
    Kubernetes components actually declared — possibly in a different
    manifest file — producing a `uses` (for ConfigMap/Secret/PVC/
    ServiceAccount/HPA target) or `connects_to` (for Ingress -> Service)
    relationship per real match. Also produces Namespace -> component
    `contains` relationships (Phase 6D.4) — see below for why that one
    relationship is deliberately NOT namespace/directory-scoped the same
    way everything else here is. Called once from
    ikm_service.build_infrastructure_model(), after every file has
    already been parsed.

    NAMESPACE SCOPING (for everything except Namespace `contains`): the
    lookup key includes each component's own namespace
    (component.metadata.get("namespace"), None if absent), and a
    reference is always resolved against the REFERENCING component's own
    namespace — never a separately-specified one. This matches real
    Kubernetes semantics: a Pod can only reference a ConfigMap/Secret/PVC/
    ServiceAccount in its own namespace, an Ingress backend can only
    reference a Service in its own namespace, and an HPA can only scale a
    workload in its own namespace; cross-namespace references of these
    kinds aren't something core Kubernetes supports. "Unspecified"
    namespace is its own equivalence class — it matches another
    unspecified namespace, but never an explicit one (see
    KubernetesParser._parse_document's namespace-capture comment for why
    a missing namespace is never coerced to "default").

    NAMESPACE `contains` IS THE ONE EXCEPTION: a Namespace is itself a
    cluster-wide Kubernetes resource, not tied to any repository
    directory or file — so matching a component's `namespace` field
    against a declared Namespace component's name is done repo-wide, not
    scoped by directory the way every other relationship in this project
    is. This is a deliberate, evidence-backed exception (see this
    module's top docstring), not an inconsistency to "fix."
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
            for ref_name in component.metadata.get("pvc_refs", []):
                target = by_namespace_name_and_kind.get((namespace, ref_name, "PersistentVolumeClaim"))
                if target is not None:
                    relationships.append(
                        Relationship(source=component.id, target=target.id, relationship_type=RelationshipType.USES)
                    )
            service_account_name = component.metadata.get("service_account_ref")
            if service_account_name:
                target = by_namespace_name_and_kind.get((namespace, service_account_name, "ServiceAccount"))
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
        elif kind == "HorizontalPodAutoscaler":
            target_kind = component.metadata.get("scale_target_kind")
            target_name = component.metadata.get("scale_target_name")
            # Only resolved when the named kind is a real, valid HPA
            # scale target (see _HPA_VALID_TARGET_KINDS) — an HPA naming
            # an unsupported kind (e.g. a typo, or a kind Kubernetes
            # itself wouldn't accept as a scale target) never produces a
            # relationship, rather than guessing at one.
            if target_kind in _HPA_VALID_TARGET_KINDS and target_name:
                target = by_namespace_name_and_kind.get((namespace, target_name, target_kind))
                if target is not None:
                    relationships.append(
                        Relationship(source=component.id, target=target.id, relationship_type=RelationshipType.USES)
                    )

    # --- Namespace -> component `contains` relationships (Phase 6D.4) ---
    # Deliberately NOT directory-scoped — see this module's docstring for
    # why: a Namespace is a cluster-wide Kubernetes concept, not tied to
    # any repository directory, and its members are commonly declared
    # across many different files/directories in a real repo. Every
    # OTHER Kubernetes component's own `namespace` field is matched
    # against every declared Namespace component's name, repo-wide.
    namespaces_by_name = {c.name: c for c in k8s_components if c.metadata.get("kind") == "Namespace"}
    if namespaces_by_name:
        for component in k8s_components:
            if component.metadata.get("kind") == "Namespace":
                continue
            namespace_name = component.metadata.get("namespace")
            if not namespace_name:
                continue
            namespace_component = namespaces_by_name.get(namespace_name)
            if namespace_component is not None:
                relationships.append(
                    Relationship(
                        source=namespace_component.id, target=component.id, relationship_type=RelationshipType.CONTAINS
                    )
                )

    return relationships
