"""Phase 7F: Kubernetes security-field parser enrichment.

Parser-enrichment-only phase — no new security rule, no new endpoint, no
GraphEngine/analysis_service change. Adds six new, additive
Component.metadata fields the existing app.security.rules engine (or a
future rule) can build real Kubernetes security rules from, since
Phase 7E's audit found the parser genuinely didn't capture any of these
facts before now.

Real Kubernetes API facts this file's tests are written against (see
app/parsers/kubernetes_parser.py's own docstrings for the full
reasoning):

- `privileged` exists ONLY in a container's own SecurityContext — there
  is no pod-level `privileged` field in the real Kubernetes API at all.
- `runAsNonRoot` exists at BOTH pod (PodSecurityContext) and container
  (SecurityContext) level, with an explicit container-level value always
  taking precedence over the pod-level one for that container.
- A Secret manifest has NO `spec` field — `type`/`data`/`stringData` are
  document-root fields, siblings of `metadata`, not nested under `spec`.
- An omitted Service `type` defaults to "ClusterIP" (matching real
  Kubernetes' own server-side default), so that field is always present.
  `privileged`/`run_as_non_root` are genuinely three-valued
  (True/False/never-set) and are OMITTED from metadata when never set,
  not defaulted to a guess — the same "omit rather than placeholder"
  convention this parser already used for `namespace`/`selector`/etc.
"""

from pathlib import Path

from app.parsers.kubernetes_parser import KubernetesParser
from tests.conftest import write


def _parse(tmp_repo: Path, filename: str, yaml_text: str):
    path = write(tmp_repo, filename, yaml_text)
    return KubernetesParser().parse(path, tmp_repo).components


# --- Ingress.has_tls -------------------------------------------------------------


def test_ingress_with_non_empty_tls_has_tls_true(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "ingress.yaml",
        """
        kind: Ingress
        metadata:
          name: web
        spec:
          tls:
            - hosts: [example.com]
              secretName: web-tls
          rules: []
        """,
    )
    assert components[0].metadata["has_tls"] is True


def test_ingress_with_no_tls_key_has_tls_false(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "ingress.yaml",
        """
        kind: Ingress
        metadata:
          name: web
        spec:
          rules: []
        """,
    )
    assert components[0].metadata["has_tls"] is False


def test_ingress_with_empty_tls_list_has_tls_false(tmp_repo: Path) -> None:
    """`tls: []` (present but empty) means no TLS is actually
    configured, same real-world fact as an absent key entirely."""
    components = _parse(
        tmp_repo,
        "ingress.yaml",
        """
        kind: Ingress
        metadata:
          name: web
        spec:
          tls: []
          rules: []
        """,
    )
    assert components[0].metadata["has_tls"] is False


def test_ingress_has_tls_is_always_present_never_omitted(tmp_repo: Path) -> None:
    """Unlike privileged/run_as_non_root, has_tls is a definite yes/no
    fact -- always set, never a three-valued omission."""
    components = _parse(tmp_repo, "ingress.yaml", "kind: Ingress\nmetadata:\n  name: web\nspec:\n  rules: []\n")
    assert "has_tls" in components[0].metadata


# --- Service.service_type ---------------------------------------------------------


def test_service_type_captured_verbatim_for_each_real_type(tmp_repo: Path) -> None:
    for index, service_type in enumerate(["LoadBalancer", "NodePort", "ClusterIP", "ExternalName"]):
        components = _parse(
            tmp_repo,
            f"svc-{index}.yaml",
            f"""
            kind: Service
            metadata:
              name: svc
            spec:
              type: {service_type}
              selector:
                app: svc
            """,
        )
        assert components[0].metadata["service_type"] == service_type


def test_service_with_no_type_key_defaults_to_cluster_ip(tmp_repo: Path) -> None:
    """Matches real Kubernetes' own server-side default for an omitted
    Service `type` -- not an invented default."""
    components = _parse(
        tmp_repo,
        "svc.yaml",
        """
        kind: Service
        metadata:
          name: svc
        spec:
          selector:
            app: svc
        """,
    )
    assert components[0].metadata["service_type"] == "ClusterIP"


# --- Secret.secret_type / Secret.has_data -----------------------------------------


def test_secret_type_and_has_data_captured_for_tls_secret(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "secret.yaml",
        """
        kind: Secret
        metadata:
          name: tls-secret
        type: kubernetes.io/tls
        data:
          tls.crt: LS0tLS1CRUdJTi0tLS0K
          tls.key: LS0tLS1CRUdJTi0tLS0K
        """,
    )
    assert components[0].metadata["secret_type"] == "kubernetes.io/tls"
    assert components[0].metadata["has_data"] is True


def test_secret_data_keys_and_values_never_appear_anywhere_in_metadata(tmp_repo: Path) -> None:
    """SAFETY INVARIANT, tested explicitly and by name rather than as an
    incidental assertion (per this phase's explicit requirement): no
    Secret data key name or value may ever be copied into
    Component.metadata -- only whether data/stringData is present at
    all."""
    components = _parse(
        tmp_repo,
        "secret.yaml",
        """
        kind: Secret
        metadata:
          name: db-secret
        type: Opaque
        data:
          username: YWRtaW4=
          password: c3VwZXJzZWNyZXQ=
        stringData:
          connection-string: postgres://admin:supersecret@db:5432/app
        """,
    )
    metadata = components[0].metadata
    assert "data" not in metadata
    assert "stringData" not in metadata
    serialized = str(metadata)
    for leaked_value in ("YWRtaW4=", "c3VwZXJzZWNyZXQ=", "supersecret", "connection-string", "username", "password"):
        assert leaked_value not in serialized, f"'{leaked_value}' leaked into Component.metadata"
    assert metadata["has_data"] is True  # presence is still correctly captured


def test_secret_with_no_data_or_string_data_has_data_false(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo, "secret.yaml", "kind: Secret\nmetadata:\n  name: empty-secret\ntype: Opaque\n"
    )
    assert components[0].metadata["has_data"] is False


def test_secret_with_empty_data_dict_has_data_false(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo, "secret.yaml", "kind: Secret\nmetadata:\n  name: empty-data\ndata: {}\n"
    )
    assert components[0].metadata["has_data"] is False


def test_secret_string_data_also_triggers_has_data_true(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "secret.yaml",
        "kind: Secret\nmetadata:\n  name: string-secret\nstringData:\n  password: hunter2\n",
    )
    assert components[0].metadata["has_data"] is True


def test_secret_type_omitted_when_absent_not_defaulted(tmp_repo: Path) -> None:
    """Unlike Service.service_type, Secret.secret_type has no documented
    default-when-absent behavior in this phase's spec -- omitted, not
    guessed at."""
    components = _parse(
        tmp_repo, "secret.yaml", "kind: Secret\nmetadata:\n  name: no-type\ndata:\n  key: dmFsdWU=\n"
    )
    assert "secret_type" not in components[0].metadata


def test_secret_fields_read_from_document_root_not_spec(tmp_repo: Path) -> None:
    """Real Kubernetes fact: Secret has no `spec` at all. A stray `spec`
    key (e.g. a hand-authored mistake) must not be where type/data are
    read from."""
    components = _parse(
        tmp_repo,
        "secret.yaml",
        """
        kind: Secret
        metadata:
          name: weird-secret
        spec:
          type: this-is-not-where-type-lives
        type: Opaque
        data:
          key: dmFsdWU=
        """,
    )
    assert components[0].metadata["secret_type"] == "Opaque"


# --- Workload.privileged -----------------------------------------------------------


def test_container_level_privileged_true_is_captured(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "deploy.yaml",
        """
        kind: Deployment
        metadata:
          name: app
        spec:
          template:
            metadata:
              labels: {app: app}
            spec:
              containers:
                - name: main
                  image: myapp:1.0
                  securityContext:
                    privileged: true
        """,
    )
    assert components[0].metadata["privileged"] is True


def test_no_security_context_anywhere_privileged_is_omitted(tmp_repo: Path) -> None:
    """Absence is a distinct fact from explicit false -- omitted (None
    via .get()), not defaulted to False."""
    components = _parse(
        tmp_repo,
        "deploy.yaml",
        """
        kind: Deployment
        metadata:
          name: app
        spec:
          template:
            metadata:
              labels: {app: app}
            spec:
              containers:
                - name: main
                  image: myapp:1.0
        """,
    )
    assert "privileged" not in components[0].metadata
    assert components[0].metadata.get("privileged") is None


def test_privileged_has_no_pod_level_fallback() -> None:
    """Documents the real API fact directly: `privileged` is a
    container-only SecurityContext field. A pod-level `securityContext`
    block has no such field to read in real Kubernetes, so this parser
    correctly never looks for one."""
    from app.parsers.kubernetes_parser import _effective_privileged

    container_without_own_security_context = {"name": "main", "image": "myapp:1.0"}
    assert _effective_privileged(container_without_own_security_context) is None


def test_privileged_worst_case_wins_across_multiple_containers(tmp_repo: Path) -> None:
    """One privileged container among several flags the whole workload,
    regardless of the others -- a pod is only as safe as its least safe
    container."""
    components = _parse(
        tmp_repo,
        "deploy.yaml",
        """
        kind: Deployment
        metadata:
          name: app
        spec:
          template:
            metadata:
              labels: {app: app}
            spec:
              containers:
                - name: a
                  image: a:1.0
                  securityContext:
                    privileged: false
                - name: b
                  image: b:1.0
                  securityContext:
                    privileged: true
        """,
    )
    assert components[0].metadata["privileged"] is True


def test_privileged_false_when_all_containers_explicitly_confirm_it(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "deploy.yaml",
        """
        kind: Deployment
        metadata:
          name: app
        spec:
          template:
            metadata:
              labels: {app: app}
            spec:
              containers:
                - name: a
                  image: a:1.0
                  securityContext:
                    privileged: false
                - name: b
                  image: b:1.0
                  securityContext:
                    privileged: false
        """,
    )
    assert components[0].metadata["privileged"] is False


def test_privileged_omitted_when_mixed_confirmed_and_unknown(tmp_repo: Path) -> None:
    """One container explicitly false, another never states it: can't
    confirm the whole workload is safe, and there's no evidence it's
    unsafe either -- stays omitted (None), not coerced into a guess."""
    components = _parse(
        tmp_repo,
        "deploy.yaml",
        """
        kind: Deployment
        metadata:
          name: app
        spec:
          template:
            metadata:
              labels: {app: app}
            spec:
              containers:
                - name: a
                  image: a:1.0
                  securityContext:
                    privileged: false
                - name: b
                  image: b:1.0
        """,
    )
    assert "privileged" not in components[0].metadata


# --- Workload.run_as_non_root: real pod/container precedence ----------------------


def test_pod_level_run_as_non_root_inherited_when_container_does_not_override(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "deploy.yaml",
        """
        kind: Deployment
        metadata:
          name: app
        spec:
          template:
            metadata:
              labels: {app: app}
            spec:
              securityContext:
                runAsNonRoot: true
              containers:
                - name: main
                  image: myapp:1.0
        """,
    )
    assert components[0].metadata["run_as_non_root"] is True


def test_container_level_run_as_non_root_overrides_pod_level() -> None:
    """The real Kubernetes precedence rule, tested directly against the
    helper that implements it: an explicit container-level value always
    wins over the pod-level one for that container."""
    from app.parsers.kubernetes_parser import _effective_run_as_non_root

    container_overriding_pod = {"name": "main", "securityContext": {"runAsNonRoot": False}}
    pod_security_context = {"runAsNonRoot": True}
    assert _effective_run_as_non_root(container_overriding_pod, pod_security_context) is False


def test_container_level_run_as_non_root_override_reflected_in_parsed_metadata(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "deploy.yaml",
        """
        kind: Deployment
        metadata:
          name: app
        spec:
          template:
            metadata:
              labels: {app: app}
            spec:
              securityContext:
                runAsNonRoot: true
              containers:
                - name: main
                  image: myapp:1.0
                  securityContext:
                    runAsNonRoot: false
        """,
    )
    assert components[0].metadata["run_as_non_root"] is False


def test_run_as_non_root_omitted_when_neither_pod_nor_container_sets_it(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "deploy.yaml",
        """
        kind: Deployment
        metadata:
          name: app
        spec:
          template:
            metadata:
              labels: {app: app}
            spec:
              containers:
                - name: main
                  image: myapp:1.0
        """,
    )
    assert "run_as_non_root" not in components[0].metadata


def test_run_as_non_root_worst_case_wins_across_multiple_containers(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "deploy.yaml",
        """
        kind: Deployment
        metadata:
          name: app
        spec:
          template:
            metadata:
              labels: {app: app}
            spec:
              containers:
                - name: a
                  image: a:1.0
                  securityContext:
                    runAsNonRoot: true
                - name: b
                  image: b:1.0
                  securityContext:
                    runAsNonRoot: false
        """,
    )
    assert components[0].metadata["run_as_non_root"] is False


# --- pre-existing behavior unaffected (regression) --------------------------------


def test_pre_existing_fields_unaffected_by_new_extraction(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "deploy.yaml",
        """
        kind: Deployment
        metadata:
          name: app
          namespace: prod
        spec:
          template:
            metadata:
              labels: {app: app}
            spec:
              serviceAccountName: my-sa
              containers:
                - name: main
                  image: myapp:2.0
                  ports:
                    - containerPort: 8080
        """,
    )
    metadata = components[0].metadata
    assert metadata["namespace"] == "prod"
    assert metadata["service_account_ref"] == "my-sa"
    assert metadata["images"] == ["myapp:2.0"]
    assert metadata["ports"] == [8080]


def test_non_workload_non_ingress_non_secret_non_service_kinds_unaffected(tmp_repo: Path) -> None:
    """ConfigMap/PVC/ServiceAccount/Namespace/HPA get none of these new
    fields -- confirming the new extraction is correctly scoped to only
    the kinds it applies to."""
    components = _parse(
        tmp_repo,
        "configmap.yaml",
        "kind: ConfigMap\nmetadata:\n  name: app-config\ndata:\n  key: value\n",
    )
    metadata = components[0].metadata
    for new_field in ("has_tls", "service_type", "secret_type", "has_data", "privileged", "run_as_non_root"):
        assert new_field not in metadata
