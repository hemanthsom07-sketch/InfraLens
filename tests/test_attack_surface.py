"""Phase 9: tests for app/security/attack_surface.py.

Unlike app/security/rules.py's 9 metadata-only rules, this module
genuinely needs a real GraphEngine (specifically its shortest_path()
method, and transitively the real Service->Workload inference it
depends on) -- so every test here builds a real GraphEngine via
GraphEngine.from_infrastructure_model(), the same pattern
tests/conftest.py's other fixtures already use, rather than mocking
graph_engine away.

Fixture shape used throughout (matches the real Kubernetes id/metadata
conventions exactly -- see app/parsers/kubernetes_parser.py):

    Ingress 'web-ingress' --connects_to--> Service 'web-svc'   (parsed)
    Service 'web-svc'     --connects_to--> Deployment 'web'    (inferred, high confidence)
    Deployment 'web'      --uses-->        Secret 'db-secret'  (parsed)
"""

from app.graph.engine import GraphEngine
from app.models.ikm import Component, InfrastructureModel, Relationship
from app.security.attack_surface import check_attack_surface


def _k8s(kind: str, name: str, metadata: dict | None = None) -> Component:
    merged = {"source_file": "k8s.yaml", "kind": kind, **(metadata or {})}
    return Component(
        id=f"kubernetes:k8s.yaml:{kind}:{name}", name=name, type="kubernetes_resource",
        technology="kubernetes", metadata=merged,
    )


def _build(components: list[Component], relationships: list[Relationship]) -> tuple[list[Component], GraphEngine]:
    model = InfrastructureModel(components=components, relationships=relationships)
    engine = GraphEngine.from_infrastructure_model(model, infer=True)
    return components, engine


def _full_chain(*, has_tls: bool = False, namespace: str = "prod"):
    """The complete, realistic Ingress -> Service -> Deployment -> Secret
    chain, with has_tls parameterized for the positive/negative TLS
    tests."""
    ingress = _k8s("Ingress", "web-ingress", {"has_tls": has_tls, "service_refs": ["web-svc"], "namespace": namespace})
    service = _k8s("Service", "web-svc", {"service_type": "ClusterIP", "selector": {"app": "web"}, "namespace": namespace})
    deployment = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["db-secret"], "namespace": namespace})
    secret = _k8s("Secret", "db-secret", {"namespace": namespace, "secret_type": "Opaque", "has_data": True})
    relationships = [
        Relationship(source=ingress.id, target=service.id, relationship_type="connects_to"),
        Relationship(source=deployment.id, target=secret.id, relationship_type="uses"),
    ]
    return ingress, service, deployment, secret, relationships


# --- positive cases: each exposure mechanism -------------------------------------


def test_tls_less_ingress_produces_a_finding() -> None:
    ingress, service, deployment, secret, rels = _full_chain(has_tls=False)
    components, engine = _build([ingress, service, deployment, secret], rels)

    findings = check_attack_surface(components, engine)

    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "EXPOSED_PATH_TO_SECRET"


def test_node_port_service_produces_a_finding_with_no_ingress_at_all() -> None:
    service = _k8s("Service", "web-svc", {"service_type": "NodePort", "selector": {"app": "web"}, "namespace": "prod"})
    deployment = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["db-secret"], "namespace": "prod"})
    secret = _k8s("Secret", "db-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    rels = [Relationship(source=deployment.id, target=secret.id, relationship_type="uses")]
    components, engine = _build([service, deployment, secret], rels)

    findings = check_attack_surface(components, engine)

    assert len(findings) == 1
    assert findings[0].subject_id == service.id


def test_load_balancer_service_produces_a_finding_with_no_ingress_at_all() -> None:
    service = _k8s("Service", "web-svc", {"service_type": "LoadBalancer", "selector": {"app": "web"}, "namespace": "prod"})
    deployment = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["db-secret"], "namespace": "prod"})
    secret = _k8s("Secret", "db-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    rels = [Relationship(source=deployment.id, target=secret.id, relationship_type="uses")]
    components, engine = _build([service, deployment, secret], rels)

    findings = check_attack_surface(components, engine)

    assert len(findings) == 1


# --- negative cases ----------------------------------------------------------------


def test_tls_enabled_ingress_produces_no_finding() -> None:
    ingress, service, deployment, secret, rels = _full_chain(has_tls=True)
    components, engine = _build([ingress, service, deployment, secret], rels)

    findings = check_attack_surface(components, engine)

    assert findings == []


def test_cluster_ip_with_no_ingress_produces_no_finding() -> None:
    service = _k8s("Service", "web-svc", {"service_type": "ClusterIP", "selector": {"app": "web"}, "namespace": "prod"})
    deployment = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["db-secret"], "namespace": "prod"})
    secret = _k8s("Secret", "db-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    rels = [Relationship(source=deployment.id, target=secret.id, relationship_type="uses")]
    components, engine = _build([service, deployment, secret], rels)

    findings = check_attack_surface(components, engine)

    assert findings == []


def test_external_name_service_is_never_treated_as_exposed() -> None:
    service = _k8s("Service", "ext-svc", {"service_type": "ExternalName", "namespace": "prod"})
    secret = _k8s("Secret", "db-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    components, engine = _build([service, secret], [])

    findings = check_attack_surface(components, engine)

    assert findings == []


def test_service_with_no_selector_produces_no_finding() -> None:
    """No selector -> infer_service_workload_edges() creates no edge at
    all -> shortest_path() correctly finds nothing."""
    service = _k8s("Service", "web-svc", {"service_type": "LoadBalancer", "namespace": "prod"})  # no selector key
    deployment = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["db-secret"], "namespace": "prod"})
    secret = _k8s("Secret", "db-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    rels = [Relationship(source=deployment.id, target=secret.id, relationship_type="uses")]
    components, engine = _build([service, deployment, secret], rels)

    findings = check_attack_surface(components, engine)

    assert findings == []


def test_selector_matching_no_workload_produces_no_finding() -> None:
    service = _k8s("Service", "web-svc", {"service_type": "LoadBalancer", "selector": {"app": "does-not-exist"}, "namespace": "prod"})
    deployment = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["db-secret"], "namespace": "prod"})
    secret = _k8s("Secret", "db-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    rels = [Relationship(source=deployment.id, target=secret.id, relationship_type="uses")]
    components, engine = _build([service, deployment, secret], rels)

    findings = check_attack_surface(components, engine)

    assert findings == []


def test_workload_without_secret_reference_produces_no_finding() -> None:
    service = _k8s("Service", "web-svc", {"service_type": "LoadBalancer", "selector": {"app": "web"}, "namespace": "prod"})
    deployment = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "namespace": "prod"})  # no secret_refs
    secret = _k8s("Secret", "db-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    components, engine = _build([service, deployment, secret], [])

    findings = check_attack_surface(components, engine)

    assert findings == []


def test_exposed_entry_with_disconnected_secret_produces_no_finding() -> None:
    """An exposed Service and a Secret both exist in the repo, but
    nothing connects them at all -- no workload, no reference."""
    service = _k8s("Service", "web-svc", {"service_type": "LoadBalancer", "namespace": "prod"})
    secret = _k8s("Secret", "unrelated-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    components, engine = _build([service, secret], [])

    findings = check_attack_surface(components, engine)

    assert findings == []


def test_cross_namespace_service_and_workload_produces_no_finding() -> None:
    service = _k8s("Service", "web-svc", {"service_type": "LoadBalancer", "selector": {"app": "web"}, "namespace": "ns-a"})
    deployment = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["db-secret"], "namespace": "ns-b"})
    secret = _k8s("Secret", "db-secret", {"namespace": "ns-b", "secret_type": "Opaque", "has_data": True})
    rels = [Relationship(source=deployment.id, target=secret.id, relationship_type="uses")]
    components, engine = _build([service, deployment, secret], rels)

    findings = check_attack_surface(components, engine)

    assert findings == []


def test_no_kubernetes_components_produces_no_finding() -> None:
    compose = Component(
        id="compose:x:app", name="app", type="service", technology="docker-compose",
        metadata={"source_file": "x", "image": "myapp:1.0"},
    )
    components, engine = _build([compose], [])

    assert check_attack_surface(components, engine) == []


# --- multiplicity -------------------------------------------------------------------


def test_multiple_exposed_entries_each_produce_their_own_finding() -> None:
    ingress = _k8s("Ingress", "web-ingress", {"has_tls": False, "service_refs": ["web-svc"], "namespace": "prod"})
    nodeport = _k8s("Service", "admin-svc", {"service_type": "NodePort", "selector": {"app": "admin"}, "namespace": "prod"})
    service = _k8s("Service", "web-svc", {"service_type": "ClusterIP", "selector": {"app": "web"}, "namespace": "prod"})
    web_deploy = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["db-secret"], "namespace": "prod"})
    admin_deploy = _k8s("Deployment", "admin", {"pod_labels": {"app": "admin"}, "secret_refs": ["db-secret"], "namespace": "prod"})
    secret = _k8s("Secret", "db-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    rels = [
        Relationship(source=ingress.id, target=service.id, relationship_type="connects_to"),
        Relationship(source=web_deploy.id, target=secret.id, relationship_type="uses"),
        Relationship(source=admin_deploy.id, target=secret.id, relationship_type="uses"),
    ]
    components, engine = _build([ingress, nodeport, service, web_deploy, admin_deploy, secret], rels)

    findings = check_attack_surface(components, engine)

    subject_ids = {f.subject_id for f in findings}
    assert subject_ids == {ingress.id, nodeport.id}
    assert len(findings) == 2


def test_multiple_reachable_secrets_produce_multiple_findings() -> None:
    service = _k8s("Service", "web-svc", {"service_type": "LoadBalancer", "selector": {"app": "web"}, "namespace": "prod"})
    deployment = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["secret-a", "secret-b"], "namespace": "prod"})
    secret_a = _k8s("Secret", "secret-a", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    secret_b = _k8s("Secret", "secret-b", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    rels = [
        Relationship(source=deployment.id, target=secret_a.id, relationship_type="uses"),
        Relationship(source=deployment.id, target=secret_b.id, relationship_type="uses"),
    ]
    components, engine = _build([service, deployment, secret_a, secret_b], rels)

    findings = check_attack_surface(components, engine)

    assert len(findings) == 2
    assert all(f.subject_id == service.id for f in findings)


# --- ordering, severity, id, attachment ---------------------------------------------


def test_findings_are_deterministically_ordered() -> None:
    service = _k8s("Service", "web-svc", {"service_type": "LoadBalancer", "selector": {"app": "web"}, "namespace": "prod"})
    deployment = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["z-secret", "a-secret"], "namespace": "prod"})
    secret_z = _k8s("Secret", "z-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    secret_a = _k8s("Secret", "a-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    rels = [
        Relationship(source=deployment.id, target=secret_z.id, relationship_type="uses"),
        Relationship(source=deployment.id, target=secret_a.id, relationship_type="uses"),
    ]
    components, engine = _build([service, deployment, secret_z, secret_a], rels)

    first = check_attack_surface(components, engine)
    second = check_attack_surface(components, engine)

    assert [f.detail["reason"] for f in first] == [f.detail["reason"] for f in second]
    ids_in_reason_order = [secret_a.id if secret_a.name in f.detail["reason"] else secret_z.id for f in first]
    assert ids_in_reason_order == sorted([secret_a.id, secret_z.id])


def test_finding_severity_is_high() -> None:
    ingress, service, deployment, secret, rels = _full_chain()
    components, engine = _build([ingress, service, deployment, secret], rels)

    findings = check_attack_surface(components, engine)

    assert findings[0].detail["severity"] == "high"


def test_finding_rule_id_is_exact() -> None:
    ingress, service, deployment, secret, rels = _full_chain()
    components, engine = _build([ingress, service, deployment, secret], rels)

    findings = check_attack_surface(components, engine)

    assert findings[0].detail["rule_id"] == "EXPOSED_PATH_TO_SECRET"


def test_finding_attaches_to_the_exposed_entry_not_the_secret() -> None:
    ingress, service, deployment, secret, rels = _full_chain()
    components, engine = _build([ingress, service, deployment, secret], rels)

    findings = check_attack_surface(components, engine)

    assert findings[0].subject_id == ingress.id
    assert findings[0].subject_id != secret.id


# --- reason wording: hop sequence + honest provenance --------------------------


def test_reason_contains_the_actual_hop_sequence() -> None:
    ingress, service, deployment, secret, rels = _full_chain()
    components, engine = _build([ingress, service, deployment, secret], rels)

    reason = check_attack_surface(components, engine)[0].detail["reason"]

    assert "web-ingress" in reason
    assert "web-svc" in reason
    assert "web" in reason
    assert "db-secret" in reason


def test_reason_labels_service_to_workload_hop_as_inferred_not_parsed() -> None:
    """The one hop that must NOT be described as directly parsed."""
    ingress, service, deployment, secret, rels = _full_chain()
    components, engine = _build([ingress, service, deployment, secret], rels)

    reason = check_attack_surface(components, engine)[0].detail["reason"]

    assert "inferred, high-confidence label-selector match" in reason


def test_reason_labels_ingress_to_service_and_workload_to_secret_hops_as_parsed() -> None:
    ingress, service, deployment, secret, rels = _full_chain()
    components, engine = _build([ingress, service, deployment, secret], rels)

    reason = check_attack_surface(components, engine)[0].detail["reason"]

    # Exactly two hops should be labeled parsed (Ingress->Service,
    # Workload->Secret) and exactly one inferred (Service->Workload).
    assert reason.count("(parsed)") == 2
    assert reason.count("(inferred, high-confidence label-selector match)") == 1


def test_direct_service_exposure_reason_has_no_ingress_hop_but_still_labels_inference_correctly() -> None:
    """For a directly-exposed Service (no Ingress), the path is only 3
    nodes -- confirming the hop-labeling logic is robust to path length,
    not hardcoded to assume an Ingress is always present."""
    service = _k8s("Service", "web-svc", {"service_type": "LoadBalancer", "selector": {"app": "web"}, "namespace": "prod"})
    deployment = _k8s("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["db-secret"], "namespace": "prod"})
    secret = _k8s("Secret", "db-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    rels = [Relationship(source=deployment.id, target=secret.id, relationship_type="uses")]
    components, engine = _build([service, deployment, secret], rels)

    reason = check_attack_surface(components, engine)[0].detail["reason"]

    assert reason.count("(parsed)") == 1  # Workload -> Secret only
    assert reason.count("(inferred, high-confidence label-selector match)") == 1  # Service -> Workload


# --- safety: no Secret data ever touched ----------------------------------------


def test_secret_data_never_appears_in_finding_output() -> None:
    ingress, service, deployment, secret, rels = _full_chain()
    # Even though has_data=True is set, the actual data/stringData keys
    # and values are never captured anywhere in Component.metadata by
    # the parser (Phase 7F's own invariant) -- confirming this module
    # can't leak what it never receives. The finding's detail only ever
    # contains rule_id/severity/title/reason/component_name/technology/
    # source_file (see app.security.rules._finding()) -- never a raw
    # metadata dump of any component.
    components, engine = _build([ingress, service, deployment, secret], rels)

    finding = check_attack_surface(components, engine)[0]

    assert set(finding.detail.keys()) == {
        "rule_id", "severity", "title", "reason", "component_name", "technology", "source_file",
    }
    assert "stringData" not in str(finding.detail)
