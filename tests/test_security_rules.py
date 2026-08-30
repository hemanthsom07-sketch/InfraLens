"""Phase 7E: tests for app/security/rules.py.

Each rule is tested at two levels: the pure helper function (parsing/
classification logic in isolation) and the rule function itself (over a
small list of real Component objects), matching the granularity the
live-execution check already used while building this module.
"""

from app.explanation.evidence import ObservationKind
from app.models.ikm import Component
from app.security.rules import (
    Severity,
    _check_containers_allowed_to_run_as_root,
    _check_database_ports_published,
    _check_docker_socket_mounts,
    _check_externally_reachable_services,
    _check_hardcoded_secrets,
    _check_ingress_without_tls,
    _check_mutable_image_tags,
    _check_privileged_containers,
    _check_sensitive_ports_published,
    _has_mutable_tag,
    _image_repository_name,
    _is_docker_socket_mount,
    _is_known_database_image,
    _looks_like_hardcoded_secret,
    _sensitive_host_port,
    run_security_rules,
)
from app.models.ikm import InfrastructureModel


def _component(technology: str, metadata: dict, *, id: str = "x:1", type: str = "service") -> Component:
    return Component(id=id, name="x", type=type, technology=technology, metadata=metadata)


def _k8s_component(kind: str, metadata: dict | None = None, *, id: str | None = None) -> Component:
    """A Kubernetes Component for a given manifest `kind`, with the
    Phase 7F metadata fields merged in — mirrors what
    KubernetesParser._parse_document() actually produces, without going
    through the real parser (these are unit tests of the rule functions,
    not the parser)."""
    merged = {"source_file": "app.yaml", "kind": kind, **(metadata or {})}
    return Component(
        id=id or f"kubernetes:app.yaml:{kind}:c",
        name="c",
        type="kubernetes_resource",
        technology="kubernetes",
        metadata=merged,
    )


# --- Rule 1 helper: _has_mutable_tag --------------------------------------------


def test_mutable_tag_flags_no_tag_or_explicit_latest() -> None:
    assert _has_mutable_tag("postgres") is True  # no tag at all -> implicit :latest
    assert _has_mutable_tag("myapp:latest") is True  # explicit :latest


def test_mutable_tag_does_not_flag_pinned_or_digest_referenced_images() -> None:
    assert _has_mutable_tag("postgres:16") is False
    assert _has_mutable_tag("myapp@sha256:abcd1234") is False  # digest-pinned, regardless of tag
    assert _has_mutable_tag("myapp:latest@sha256:abcd1234") is False


def test_mutable_tag_registry_port_is_never_mistaken_for_a_tag_separator() -> None:
    assert _has_mutable_tag("registry.example.com:5000/myapp") is True  # no real tag -> still mutable
    assert _has_mutable_tag("registry.example.com:5000/myapp:1.0") is False  # real tag present


# --- Rule 1: mutable image tags across technologies -----------------------------


def test_compose_service_with_latest_tag_is_flagged() -> None:
    component = _component("docker-compose", {"source_file": "docker-compose.yml", "image": "myapp:latest"})
    findings = _check_mutable_image_tags([component])
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "MUTABLE_IMAGE_TAG"
    assert findings[0].kind == ObservationKind.SECURITY_FINDING


def test_compose_service_with_pinned_tag_is_not_flagged() -> None:
    component = _component("docker-compose", {"source_file": "docker-compose.yml", "image": "myapp:1.2.3"})
    assert _check_mutable_image_tags([component]) == []


def test_compose_service_with_no_image_is_not_flagged() -> None:
    """A build-only service (no `image:` key) has nothing to check --
    not an invented finding."""
    component = _component("docker-compose", {"source_file": "docker-compose.yml", "image": None})
    assert _check_mutable_image_tags([component]) == []


def test_docker_dockerfile_with_latest_base_image_is_flagged() -> None:
    component = _component("docker", {"source_file": "Dockerfile", "base_image": "python"})
    findings = _check_mutable_image_tags([component])
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "MUTABLE_IMAGE_TAG"


def test_docker_dockerfile_with_pinned_base_image_is_not_flagged() -> None:
    component = _component("docker", {"source_file": "Dockerfile", "base_image": "python:3.12-slim"})
    assert _check_mutable_image_tags([component]) == []


def test_kubernetes_workload_flags_only_the_mutable_image() -> None:
    component = _component(
        "kubernetes", {"source_file": "app.yaml", "images": ["nginx:latest", "myapp:2.0"]}
    )
    findings = _check_mutable_image_tags([component])
    assert len(findings) == 1
    assert "nginx:latest" in findings[0].detail["reason"]


def test_kubernetes_workload_with_all_pinned_images_is_not_flagged() -> None:
    component = _component("kubernetes", {"source_file": "app.yaml", "images": ["nginx:1.25", "myapp:2.0"]})
    assert _check_mutable_image_tags([component]) == []


def test_terraform_component_is_never_checked_for_image_tags() -> None:
    """Terraform captures no image field at all -- this rule must not
    invent one."""
    component = _component("terraform", {"source_file": "main.tf", "resource_type": "aws_instance"})
    assert _check_mutable_image_tags([component]) == []


def test_mutable_image_tag_finding_carries_severity_and_provenance() -> None:
    component = _component(
        "docker-compose", {"source_file": "docker-compose.yml", "image": "myapp:latest"}, id="compose:x:app"
    )
    finding = _check_mutable_image_tags([component])[0]
    assert finding.detail["severity"] == Severity.MEDIUM.value
    assert finding.subject_id == "compose:x:app"
    assert finding.detail["source_file"] == "docker-compose.yml"
    assert finding.detail["technology"] == "docker-compose"


# --- Rule 2 helper: _looks_like_hardcoded_secret --------------------------------


def test_secret_key_with_hardcoded_value_detected() -> None:
    assert _looks_like_hardcoded_secret("DB_PASSWORD", "hunter2") is True
    assert _looks_like_hardcoded_secret("APIKEY", "xyz789") is True  # matches without underscore too


def test_secret_key_not_flagged_for_variable_references_or_empty_or_non_secret_keys() -> None:
    assert _looks_like_hardcoded_secret("DB_PASSWORD", "${DB_PASSWORD}") is False  # curly var reference
    assert _looks_like_hardcoded_secret("DB_PASSWORD", "$DB_PASSWORD") is False  # bare var reference
    assert _looks_like_hardcoded_secret("DB_PASSWORD", "") is False
    assert _looks_like_hardcoded_secret("DB_PASSWORD", "   ") is False
    assert _looks_like_hardcoded_secret("DEBUG", "true") is False  # not a secret-like key at all
    assert _looks_like_hardcoded_secret("PORT", "8080") is False


# --- Rule 2: hardcoded secrets across technologies ------------------------------


def test_compose_hardcoded_secret_is_flagged() -> None:
    component = _component(
        "docker-compose", {"source_file": "docker-compose.yml", "environment": {"DB_PASSWORD": "hunter2"}}
    )
    findings = _check_hardcoded_secrets([component])
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "HARDCODED_SECRET_ENV_VAR"
    assert findings[0].detail["severity"] == Severity.HIGH.value


def test_docker_hardcoded_secret_is_flagged() -> None:
    component = _component("docker", {"source_file": "Dockerfile", "environment": {"API_TOKEN": "abc123"}})
    findings = _check_hardcoded_secrets([component])
    assert len(findings) == 1


def test_kubernetes_environment_is_never_checked() -> None:
    """Kubernetes env vars aren't captured by the current parser at
    all -- this rule must not invent that field."""
    component = _component("kubernetes", {"source_file": "app.yaml", "environment": {"DB_PASSWORD": "hunter2"}})
    assert _check_hardcoded_secrets([component]) == []


def test_multiple_secret_vars_produce_multiple_findings() -> None:
    component = _component(
        "docker-compose",
        {"source_file": "x", "environment": {"DB_PASSWORD": "a", "API_KEY": "b", "DEBUG": "true"}},
    )
    findings = _check_hardcoded_secrets([component])
    assert len(findings) == 2


def test_var_reference_secret_produces_no_finding() -> None:
    component = _component(
        "docker-compose", {"source_file": "x", "environment": {"DB_PASSWORD": "${DB_PASSWORD}"}}
    )
    assert _check_hardcoded_secrets([component]) == []


# --- Rule 3: Docker socket mount -------------------------------------------------


def test_docker_socket_mount_string_detected() -> None:
    assert _is_docker_socket_mount("/var/run/docker.sock:/var/run/docker.sock") is True


def test_normal_bind_mount_not_flagged() -> None:
    assert _is_docker_socket_mount("./config:/etc/app") is False


def test_named_volume_without_colon_not_flagged() -> None:
    assert _is_docker_socket_mount("data_volume") is False


def test_compose_service_mounting_docker_socket_is_flagged() -> None:
    component = _component(
        "docker-compose", {"source_file": "x", "volumes": ["/var/run/docker.sock:/var/run/docker.sock"]}
    )
    findings = _check_docker_socket_mounts([component])
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "DOCKER_SOCKET_MOUNT"
    assert findings[0].detail["severity"] == Severity.HIGH.value


def test_compose_service_with_normal_volumes_not_flagged() -> None:
    component = _component("docker-compose", {"source_file": "x", "volumes": ["./data:/var/lib/data"]})
    assert _check_docker_socket_mounts([component]) == []


def test_docker_socket_rule_only_applies_to_compose() -> None:
    """Docker/Kubernetes parsers don't capture volume mount info at
    all -- this rule must not invent that field for them."""
    component = _component("docker", {"source_file": "Dockerfile", "volumes": ["/var/run/docker.sock:/var/run/docker.sock"]})
    assert _check_docker_socket_mounts([component]) == []


# --- Rule 4: database port published --------------------------------------------


def test_known_database_image_names() -> None:
    assert _is_known_database_image("postgres:16") is True
    assert _is_known_database_image("mysql:8") is True
    assert _is_known_database_image("redis:7") is True
    assert _is_known_database_image("myorg/myapp:1.0") is False
    assert _is_known_database_image("docker.io/library/postgres:16") is True  # registry prefix ignored


def test_image_repository_name_extraction() -> None:
    assert _image_repository_name("postgres:16") == "postgres"
    assert _image_repository_name("registry.example.com/library/postgres:16") == "postgres"
    assert _image_repository_name("myapp@sha256:abcd") == "myapp"


def test_exposed_database_port_is_flagged() -> None:
    component = _component("docker-compose", {"source_file": "x", "image": "postgres:16", "ports": ["5432:5432"]})
    findings = _check_database_ports_published([component])
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "DATABASE_PORT_PUBLISHED"
    assert findings[0].detail["severity"] == Severity.MEDIUM.value


def test_internal_database_with_no_published_ports_is_not_flagged() -> None:
    component = _component("docker-compose", {"source_file": "x", "image": "postgres:16", "ports": []})
    assert _check_database_ports_published([component]) == []


def test_non_database_image_with_published_port_is_not_flagged() -> None:
    component = _component("docker-compose", {"source_file": "x", "image": "myorg/webapp:1.0", "ports": ["80:80"]})
    assert _check_database_ports_published([component]) == []


# --- Rule 5: sensitive port published --------------------------------------------


def test_sensitive_port_parsed_from_two_and_three_part_forms() -> None:
    assert _sensitive_host_port("22:22") == 22
    assert _sensitive_host_port("127.0.0.1:22:22") == 22  # bind_ip:hostport:containerport


def test_sensitive_port_not_flagged_for_non_sensitive_bare_or_range_forms() -> None:
    assert _sensitive_host_port("8080:80") is None  # not in the sensitive-port set
    assert _sensitive_host_port("80") is None  # no explicit host port
    assert _sensitive_host_port("3000-3005:3000-3005") is None  # a range, not a single determinable port


def test_ssh_port_published_is_flagged() -> None:
    component = _component("docker-compose", {"source_file": "x", "ports": ["22:22"]})
    findings = _check_sensitive_ports_published([component])
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "SENSITIVE_PORT_PUBLISHED"


def test_normal_port_published_is_not_flagged() -> None:
    component = _component("docker-compose", {"source_file": "x", "ports": ["8080:80"]})
    assert _check_sensitive_ports_published([component]) == []


def test_multiple_sensitive_ports_produce_multiple_findings() -> None:
    component = _component("docker-compose", {"source_file": "x", "ports": ["22:22", "3389:3389", "8080:80"]})
    findings = _check_sensitive_ports_published([component])
    assert len(findings) == 2


# --- run_security_rules: aggregation, ordering -----------------------------------


def test_run_security_rules_combines_findings_from_multiple_technologies() -> None:
    compose = _component(
        "docker-compose", {"source_file": "docker-compose.yml", "image": "myapp:latest"}, id="compose:x:app"
    )
    docker = _component("docker", {"source_file": "Dockerfile", "base_image": "python"}, id="docker:Dockerfile")
    k8s = _component("kubernetes", {"source_file": "app.yaml", "images": ["nginx:latest"]}, id="kubernetes:x:app")
    model = InfrastructureModel(components=[compose, docker, k8s])

    findings = run_security_rules(model)

    technologies = {f.detail["technology"] for f in findings}
    assert technologies == {"docker-compose", "docker", "kubernetes"}


def test_run_security_rules_on_clean_model_returns_no_findings() -> None:
    component = _component("docker-compose", {"source_file": "x", "image": "myapp:1.0", "ports": [], "environment": {}})
    model = InfrastructureModel(components=[component])
    assert run_security_rules(model) == []


def test_run_security_rules_on_empty_model_returns_no_findings() -> None:
    assert run_security_rules(InfrastructureModel()) == []


def test_run_security_rules_deterministic_ordering() -> None:
    a = _component(
        "docker-compose",
        {"source_file": "x", "image": "b:latest", "environment": {"DB_PASSWORD": "hunter2"}, "ports": [], "volumes": []},
        id="compose:x:b-service",
    )
    b = _component(
        "docker-compose",
        {"source_file": "x", "image": "a:latest", "environment": {}, "ports": [], "volumes": []},
        id="compose:x:a-service",
    )
    model = InfrastructureModel(components=[a, b])

    findings = run_security_rules(model)
    pairs = [(f.subject_id, f.detail["rule_id"]) for f in findings]
    assert pairs == sorted(pairs)


def test_run_security_rules_ordering_is_stable_across_repeated_calls() -> None:
    component = _component(
        "docker-compose",
        {"source_file": "x", "image": "myapp:latest", "environment": {"DB_PASSWORD": "hunter2"}, "ports": [], "volumes": []},
    )
    model = InfrastructureModel(components=[component])

    first = [(f.subject_id, f.detail["rule_id"]) for f in run_security_rules(model)]
    second = [(f.subject_id, f.detail["rule_id"]) for f in run_security_rules(model)]
    assert first == second


def test_every_finding_has_a_recognized_severity() -> None:
    component = _component(
        "docker-compose",
        {
            "source_file": "x",
            "image": "postgres:latest",
            "ports": ["22:22", "5432:5432"],
            "environment": {"DB_PASSWORD": "hunter2"},
            "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
        },
    )
    model = InfrastructureModel(components=[component])
    findings = run_security_rules(model)
    assert len(findings) == 5  # one from each Phase 7E rule
    assert all(f.detail["severity"] in {s.value for s in Severity} for f in findings)


# =====================================================================================
# Phase 7G: Kubernetes security rules
# =====================================================================================


# --- Rule 6: Ingress without TLS -------------------------------------------------


def test_ingress_with_has_tls_false_is_flagged() -> None:
    component = _k8s_component("Ingress", {"has_tls": False})
    findings = _check_ingress_without_tls([component])
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "INGRESS_WITHOUT_TLS"
    assert findings[0].detail["severity"] == Severity.MEDIUM.value
    assert findings[0].kind == ObservationKind.SECURITY_FINDING


def test_ingress_with_has_tls_true_is_not_flagged() -> None:
    component = _k8s_component("Ingress", {"has_tls": True})
    assert _check_ingress_without_tls([component]) == []


def test_non_ingress_kubernetes_kinds_never_checked_for_tls() -> None:
    component = _k8s_component("Service", {"has_tls": False, "service_type": "ClusterIP"})
    assert _check_ingress_without_tls([component]) == []


def test_non_kubernetes_components_never_checked_for_tls() -> None:
    component = _component("docker-compose", {"source_file": "x", "kind": "Ingress", "has_tls": False})
    assert _check_ingress_without_tls([component]) == []


# --- Rule 7: externally reachable Service -----------------------------------------


def test_cluster_ip_service_is_not_flagged() -> None:
    component = _k8s_component("Service", {"service_type": "ClusterIP"})
    assert _check_externally_reachable_services([component]) == []


def test_external_name_service_is_not_flagged() -> None:
    """ExternalName is a DNS redirect, not workload exposure -- must not
    be flagged the same way NodePort/LoadBalancer are."""
    component = _k8s_component("Service", {"service_type": "ExternalName"})
    assert _check_externally_reachable_services([component]) == []


def test_node_port_service_is_flagged() -> None:
    component = _k8s_component("Service", {"service_type": "NodePort"})
    findings = _check_externally_reachable_services([component])
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "EXTERNALLY_REACHABLE_SERVICE"
    assert findings[0].detail["severity"] == Severity.LOW.value


def test_load_balancer_service_is_flagged() -> None:
    component = _k8s_component("Service", {"service_type": "LoadBalancer"})
    findings = _check_externally_reachable_services([component])
    assert len(findings) == 1


def test_externally_reachable_finding_wording_states_exposure_not_maliciousness() -> None:
    component = _k8s_component("Service", {"service_type": "NodePort"})
    reason = _check_externally_reachable_services([component])[0].detail["reason"]
    assert "NodePort" in reason
    assert "reachable" in reason.lower()
    for alarming_word in ("vulnerable", "insecure", "malicious", "attack"):
        assert alarming_word not in reason.lower()


# --- Rule 8: privileged container --------------------------------------------------


def test_privileged_true_is_flagged() -> None:
    component = _k8s_component("Deployment", {"privileged": True})
    findings = _check_privileged_containers([component])
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "PRIVILEGED_CONTAINER"
    assert findings[0].detail["severity"] == Severity.HIGH.value


def test_privileged_false_is_not_flagged() -> None:
    component = _k8s_component("Deployment", {"privileged": False})
    assert _check_privileged_containers([component]) == []


def test_privileged_absent_is_not_flagged() -> None:
    """No `privileged` key at all (the parser's own omit-when-unknown
    convention) -- must not be silently treated as True."""
    component = _k8s_component("Deployment", {})
    assert _check_privileged_containers([component]) == []


def test_privileged_truthy_non_bool_never_flagged() -> None:
    """Strict `is True`, not a truthy check -- a non-bool value (which
    the real parser never produces, but a rule must still be strict
    about) must not accidentally fire."""
    component = _k8s_component("Deployment", {"privileged": 1})
    assert _check_privileged_containers([component]) == []


# --- Rule 9: container allowed to run as root --------------------------------------


def test_run_as_non_root_false_is_flagged() -> None:
    component = _k8s_component("Deployment", {"run_as_non_root": False})
    findings = _check_containers_allowed_to_run_as_root([component])
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "CONTAINER_ALLOWED_TO_RUN_AS_ROOT"
    assert findings[0].detail["severity"] == Severity.MEDIUM.value


def test_run_as_non_root_true_is_not_flagged() -> None:
    component = _k8s_component("Deployment", {"run_as_non_root": True})
    assert _check_containers_allowed_to_run_as_root([component]) == []


def test_run_as_non_root_absent_is_not_flagged() -> None:
    """None (never set in the manifest) must NOT be silently treated as
    False -- this is the single most important boundary in this rule."""
    component = _k8s_component("Deployment", {})
    assert _check_containers_allowed_to_run_as_root([component]) == []


def test_run_as_non_root_false_reason_states_the_manifest_declaration_explicitly() -> None:
    """Per the phase's explicit requirement: the reason must say the
    manifest DECLARES runAsNonRoot: false, not assert the container
    definitely runs as root (a stronger claim this field alone can't
    prove)."""
    component = _k8s_component("Deployment", {"run_as_non_root": False})
    reason = _check_containers_allowed_to_run_as_root([component])[0].detail["reason"]
    assert "runAsNonRoot" in reason
    assert "false" in reason.lower()


# --- no Secret rule exists (deliberate, evidence-first exclusion) -----------------


def test_secret_with_data_alone_produces_no_finding() -> None:
    """has_data=True by itself proves nothing -- a Secret having data is
    normal and expected, not a security concern."""
    component = _k8s_component("Secret", {"has_data": True, "secret_type": "Opaque"})
    model = InfrastructureModel(components=[component])
    assert run_security_rules(model) == []


def test_opaque_secret_type_alone_produces_no_finding() -> None:
    """secret_type=Opaque is the single most common, entirely normal
    Secret type -- must never be treated as insecure on its own."""
    component = _k8s_component("Secret", {"secret_type": "Opaque", "has_data": True})
    model = InfrastructureModel(components=[component])
    assert run_security_rules(model) == []


# --- deterministic ordering, cross-technology, aggregation ------------------------


def test_kubernetes_findings_participate_in_deterministic_ordering() -> None:
    compose = _component(
        "docker-compose", {"source_file": "x", "image": "myapp:latest"}, id="compose:x:app-service"
    )
    ingress = _k8s_component("Ingress", {"has_tls": False}, id="kubernetes:x:Ingress:web")
    model = InfrastructureModel(components=[ingress, compose])

    findings = run_security_rules(model)
    pairs = [(f.subject_id, f.detail["rule_id"]) for f in findings]
    assert pairs == sorted(pairs)


def test_existing_docker_compose_rules_unaffected_by_kubernetes_rules() -> None:
    """Cross-technology regression: adding Kubernetes rules must not
    change any Phase 7E Compose/Docker rule's behavior."""
    compose = _component(
        "docker-compose", {"source_file": "x", "image": "myapp:1.2.3", "ports": [], "environment": {}, "volumes": []}
    )
    model = InfrastructureModel(components=[compose])
    assert run_security_rules(model) == []  # clean compose service, still zero findings


def test_run_security_rules_aggregates_kubernetes_and_compose_findings_together() -> None:
    compose = _component(
        "docker-compose", {"source_file": "x", "image": "myapp:latest"}, id="compose:x:app"
    )
    ingress = _k8s_component("Ingress", {"has_tls": False}, id="kubernetes:x:Ingress:web")
    service = _k8s_component("Service", {"service_type": "LoadBalancer"}, id="kubernetes:x:Service:web")
    privileged = _k8s_component("Deployment", {"privileged": True}, id="kubernetes:x:Deployment:app")
    root_ok = _k8s_component("Deployment", {"run_as_non_root": False}, id="kubernetes:x:Deployment:worker")

    model = InfrastructureModel(components=[compose, ingress, service, privileged, root_ok])
    findings = run_security_rules(model)

    rule_ids = {f.detail["rule_id"] for f in findings}
    assert rule_ids == {
        "MUTABLE_IMAGE_TAG",
        "INGRESS_WITHOUT_TLS",
        "EXTERNALLY_REACHABLE_SERVICE",
        "PRIVILEGED_CONTAINER",
        "CONTAINER_ALLOWED_TO_RUN_AS_ROOT",
    }
    assert len(findings) == 5

