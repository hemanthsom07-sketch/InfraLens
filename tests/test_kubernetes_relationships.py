"""Kubernetes: Deployment/StatefulSet -> ConfigMap/Secret, and
Ingress -> Service, including across multiple manifest files."""

from pathlib import Path

from app.parsers.kubernetes_parser import KubernetesParser, resolve_references
from tests.conftest import write


def test_kubernetes_resolves_configmap_and_secret_references(tmp_repo: Path) -> None:
    """Covers both reference styles: env-based (configMapRef) and
    volume-based (secret.secretName — the one place Kubernetes uses a
    different key name than everywhere else)."""
    deploy_path = write(
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
              volumes:
                - name: tls
                  secret:
                    secretName: tls-secret
        """,
    )
    config_path = write(
        tmp_repo,
        "config.yaml",
        """
        apiVersion: v1
        kind: ConfigMap
        metadata:
          name: app-config
        ---
        apiVersion: v1
        kind: Secret
        metadata:
          name: tls-secret
        """,
    )

    components = []
    components += KubernetesParser().parse(deploy_path, tmp_repo).components
    components += KubernetesParser().parse(config_path, tmp_repo).components
    relationships = resolve_references(components)

    assert len(relationships) == 2
    assert {r.relationship_type for r in relationships} == {"uses"}


def test_kubernetes_resolves_ingress_to_service_across_files(tmp_repo: Path) -> None:
    ingress_path = write(
        tmp_repo,
        "ingress.yaml",
        """
        apiVersion: networking.k8s.io/v1
        kind: Ingress
        metadata:
          name: web-ingress
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
    service_path = write(
        tmp_repo,
        "service.yaml",
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: web-service
        spec:
          ports:
            - port: 80
        """,
    )

    components = []
    components += KubernetesParser().parse(ingress_path, tmp_repo).components
    components += KubernetesParser().parse(service_path, tmp_repo).components
    relationships = resolve_references(components)

    assert len(relationships) == 1
    assert relationships[0].relationship_type == "connects_to"


def test_kubernetes_reference_to_undeclared_configmap_is_skipped(tmp_repo: Path) -> None:
    """Referencing a ConfigMap that was never actually declared anywhere
    in the repo shouldn't produce a dangling relationship — it should
    just be silently skipped, the same tolerant-by-default behavior used
    everywhere else in the project for data that doesn't fully resolve."""
    path = write(
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
                        name: never-declared
        """,
    )
    components = KubernetesParser().parse(path, tmp_repo).components
    relationships = resolve_references(components)
    assert relationships == []
