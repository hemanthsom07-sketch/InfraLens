"""Phase 6A.1: Kubernetes namespace scoping.

Covers the false-positive risk identified in the design phase: a Service
in one namespace matching a Deployment in a different namespace purely
because they share a name or labels, either via
kubernetes_parser.resolve_references() (ConfigMap/Secret/Ingress->Service)
or via graph/inference.py's Service->Workload label-selector inference.

Positive / negative / mixed / regression cases, per the approved design:
    - explicit namespace A matches only explicit namespace A
    - explicit namespace A does not match explicit namespace B
    - explicit namespace does not match missing namespace
    - missing namespace matches missing namespace
"""

from pathlib import Path

from app.graph.inference import infer_service_workload_edges
from app.parsers.kubernetes_parser import KubernetesParser, resolve_references
from tests.conftest import write


def _parse(tmp_repo: Path, filename: str, yaml_text: str):
    path = write(tmp_repo, filename, yaml_text)
    return KubernetesParser().parse(path, tmp_repo).components


# --- resolve_references(): ConfigMap ------------------------------------------


def test_configmap_ref_does_not_resolve_across_different_explicit_namespaces(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-app
          namespace: staging
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
                  envFrom:
                    - configMapRef:
                        name: app-config
        """,
    )
    config = _parse(
        tmp_repo,
        "config.yaml",
        """
        apiVersion: v1
        kind: ConfigMap
        metadata:
          name: app-config
          namespace: production
        """,
    )
    relationships = resolve_references(deploy + config)
    assert relationships == []


def test_configmap_ref_resolves_within_same_explicit_namespace_across_files(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-app
          namespace: staging
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
                  envFrom:
                    - configMapRef:
                        name: app-config
        """,
    )
    config = _parse(
        tmp_repo,
        "config.yaml",
        """
        apiVersion: v1
        kind: ConfigMap
        metadata:
          name: app-config
          namespace: staging
        """,
    )
    relationships = resolve_references(deploy + config)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"


def test_configmap_ref_does_not_resolve_when_only_target_has_explicit_namespace(tmp_repo: Path) -> None:
    """Mixed case: referencing Deployment has no namespace field,
    ConfigMap has an explicit one -- must NOT match (unspecified is its
    own bucket, not a wildcard)."""
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-app
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
                  envFrom:
                    - configMapRef:
                        name: app-config
        """,
    )
    config = _parse(
        tmp_repo,
        "config.yaml",
        """
        apiVersion: v1
        kind: ConfigMap
        metadata:
          name: app-config
          namespace: production
        """,
    )
    relationships = resolve_references(deploy + config)
    assert relationships == []


def test_configmap_ref_does_not_resolve_when_only_source_has_explicit_namespace(tmp_repo: Path) -> None:
    """Mixed case, other direction: referencing Deployment has an
    explicit namespace, ConfigMap has none -- must NOT match."""
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-app
          namespace: staging
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
                  envFrom:
                    - configMapRef:
                        name: app-config
        """,
    )
    config = _parse(
        tmp_repo,
        "config.yaml",
        """
        apiVersion: v1
        kind: ConfigMap
        metadata:
          name: app-config
        """,
    )
    relationships = resolve_references(deploy + config)
    assert relationships == []


# --- resolve_references(): Secret ------------------------------------------


def test_secret_ref_does_not_resolve_across_different_explicit_namespaces(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-app
          namespace: staging
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
              volumes:
                - name: tls
                  secret:
                    secretName: tls-secret
        """,
    )
    secret = _parse(
        tmp_repo,
        "secret.yaml",
        """
        apiVersion: v1
        kind: Secret
        metadata:
          name: tls-secret
          namespace: production
        """,
    )
    relationships = resolve_references(deploy + secret)
    assert relationships == []


def test_secret_ref_resolves_within_same_explicit_namespace(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-app
          namespace: staging
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
              volumes:
                - name: tls
                  secret:
                    secretName: tls-secret
        """,
    )
    secret = _parse(
        tmp_repo,
        "secret.yaml",
        """
        apiVersion: v1
        kind: Secret
        metadata:
          name: tls-secret
          namespace: staging
        """,
    )
    relationships = resolve_references(deploy + secret)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"


# --- resolve_references(): Ingress -> Service -------------------------------


def test_ingress_service_ref_does_not_resolve_across_different_explicit_namespaces(tmp_repo: Path) -> None:
    ingress = _parse(
        tmp_repo,
        "ingress.yaml",
        """
        apiVersion: networking.k8s.io/v1
        kind: Ingress
        metadata:
          name: web-ingress
          namespace: staging
        spec:
          rules:
            - http:
                paths:
                  - path: /
                    backend:
                      service:
                        name: web-service
                        port:
                          number: 80
        """,
    )
    service = _parse(
        tmp_repo,
        "service.yaml",
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: web-service
          namespace: production
        spec:
          ports:
            - port: 80
        """,
    )
    relationships = resolve_references(ingress + service)
    assert relationships == []


def test_ingress_service_ref_resolves_within_same_explicit_namespace(tmp_repo: Path) -> None:
    ingress = _parse(
        tmp_repo,
        "ingress.yaml",
        """
        apiVersion: networking.k8s.io/v1
        kind: Ingress
        metadata:
          name: web-ingress
          namespace: staging
        spec:
          rules:
            - http:
                paths:
                  - path: /
                    backend:
                      service:
                        name: web-service
                        port:
                          number: 80
        """,
    )
    service = _parse(
        tmp_repo,
        "service.yaml",
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: web-service
          namespace: staging
        spec:
          ports:
            - port: 80
        """,
    )
    relationships = resolve_references(ingress + service)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "connects_to"


# --- graph/inference.py: Service -> Workload label-selector inference ------


def test_service_workload_inference_does_not_cross_explicit_namespaces(tmp_repo: Path) -> None:
    service = _parse(
        tmp_repo,
        "service.yaml",
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: web-svc
          namespace: staging
        spec:
          selector:
            app: web
          ports:
            - port: 80
        """,
    )
    workload = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-deploy
          namespace: production
        spec:
          template:
            metadata:
              labels:
                app: web
            spec:
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    edges = infer_service_workload_edges(service + workload)
    assert edges == []


def test_service_workload_inference_matches_within_same_explicit_namespace(tmp_repo: Path) -> None:
    service = _parse(
        tmp_repo,
        "service.yaml",
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: web-svc
          namespace: staging
        spec:
          selector:
            app: web
          ports:
            - port: 80
        """,
    )
    workload = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-deploy
          namespace: staging
        spec:
          template:
            metadata:
              labels:
                app: web
            spec:
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    edges = infer_service_workload_edges(service + workload)
    assert len(edges) == 1
    assert edges[0].edge_type == "connects_to"
    assert edges[0].metadata["confidence"] == "high"
    assert edges[0].metadata["origin"] == "inferred"
    assert edges[0].metadata["basis"] == "label selector match"


def test_service_workload_inference_does_not_match_when_only_one_side_has_explicit_namespace(
    tmp_repo: Path,
) -> None:
    """Mixed case: Service has no namespace, Deployment has an explicit
    one -- must NOT match."""
    service = _parse(
        tmp_repo,
        "service.yaml",
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: web-svc
        spec:
          selector:
            app: web
          ports:
            - port: 80
        """,
    )
    workload = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-deploy
          namespace: staging
        spec:
          template:
            metadata:
              labels:
                app: web
            spec:
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    edges = infer_service_workload_edges(service + workload)
    assert edges == []


# --- regression: namespace-less fixtures behave exactly as before ----------


def test_configmap_ref_still_resolves_with_no_namespace_on_either_side(tmp_repo: Path) -> None:
    """Exact regression case: neither manifest sets a namespace field at
    all (today's fixture shape) -- must resolve exactly as it did before
    this change ("unspecified matches unspecified")."""
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-app
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
                  envFrom:
                    - configMapRef:
                        name: app-config
        """,
    )
    config = _parse(
        tmp_repo,
        "config.yaml",
        """
        apiVersion: v1
        kind: ConfigMap
        metadata:
          name: app-config
        """,
    )
    relationships = resolve_references(deploy + config)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"


def test_service_workload_inference_still_matches_with_no_namespace_on_either_side(tmp_repo: Path) -> None:
    """Exact regression case mirroring
    test_graph_pipeline.test_kubernetes_repository_produces_relationships_and_graph_edges's
    shape: no namespace field anywhere -- inference must behave exactly
    as it did before this change."""
    service = _parse(
        tmp_repo,
        "service.yaml",
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: backend-svc
        spec:
          selector:
            app: backend
          ports:
            - port: 80
        """,
    )
    workload = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: backend
        spec:
          template:
            metadata:
              labels:
                app: backend
            spec:
              containers:
                - name: backend
                  image: myapp:1.0
        """,
    )
    edges = infer_service_workload_edges(service + workload)
    assert len(edges) == 1
    assert edges[0].metadata["confidence"] == "high"


def test_namespace_metadata_absent_when_not_specified_in_manifest(tmp_repo: Path) -> None:
    """The namespace key itself should be entirely absent from metadata
    (not None, not "default") when the manifest doesn't set one --
    matches the established convention for every other optional field
    this parser captures."""
    components = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-app
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    assert "namespace" not in components[0].metadata


def test_namespace_metadata_captured_when_specified_in_manifest(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web-app
          namespace: staging
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    assert components[0].metadata["namespace"] == "staging"
