"""Phase 6D.4: Kubernetes Namespace components and `contains` relationships.

`kind: Namespace` manifests become their own component. Every OTHER
Kubernetes component whose `namespace` field names a declared Namespace
gets a `Namespace --contains--> component` relationship.

DELIBERATELY NOT DIRECTORY-SCOPED: a Kubernetes namespace is a
cluster-wide resource, not tied to a repository directory - a real repo
commonly declares its Namespace object in one file while the resources
that live in it are scattered across many other files, often in
different directories entirely. This is the one relationship in the
whole codebase that intentionally does NOT follow the directory-scoping
principle established in 6A.7/6A.2/6B/6C - several tests below exist
specifically to prove this cross-directory behavior is correct, not
accidental.
"""

from pathlib import Path

from app.parsers.kubernetes_parser import KubernetesParser, resolve_references
from tests.conftest import write


def _parse(tmp_repo: Path, filename: str, yaml_text: str):
    path = write(tmp_repo, filename, yaml_text)
    return KubernetesParser().parse(path, tmp_repo).components


# --- Namespace detection -------------------------------------------------------


def test_namespace_manifest_recognized_as_component(tmp_repo: Path) -> None:
    components = _parse(tmp_repo, "namespace.yaml", "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: staging\n")
    assert len(components) == 1
    assert components[0].metadata["kind"] == "Namespace"
    assert components[0].name == "staging"


def test_namespace_manifest_has_no_containers_or_ports(tmp_repo: Path) -> None:
    components = _parse(tmp_repo, "namespace.yaml", "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: staging\n")
    assert components[0].metadata["images"] == []
    assert components[0].metadata["ports"] == []


# --- positive: contains relationship, same file ------------------------------


def test_namespace_contains_relationship_same_file(tmp_repo: Path) -> None:
    namespace_and_deploy = _parse(
        tmp_repo,
        "manifest.yaml",
        """
        apiVersion: v1
        kind: Namespace
        metadata:
          name: staging
        ---
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
          namespace: staging
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    relationships = resolve_references(namespace_and_deploy)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "contains"

    namespace = next(c for c in namespace_and_deploy if c.metadata["kind"] == "Namespace")
    deployment = next(c for c in namespace_and_deploy if c.metadata["kind"] == "Deployment")
    assert relationships[0].source == namespace.id
    assert relationships[0].target == deployment.id


# --- positive: contains relationship, DIFFERENT directories -----------------


def test_namespace_contains_relationship_across_different_directories(tmp_repo: Path) -> None:
    """The deliberate scope-rule exception, proven directly: a Namespace
    declared at the repo root and a Deployment declared several
    directories away must still resolve - this must NOT be treated as a
    directory-scoping violation."""
    namespace = _parse(tmp_repo, "namespaces/staging.yaml", "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: staging\n")
    deploy = _parse(
        tmp_repo,
        "apps/backend/deployment/web.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
          namespace: staging
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    relationships = resolve_references(namespace + deploy)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "contains"
    assert relationships[0].source == namespace[0].id
    assert relationships[0].target == deploy[0].id


def test_namespace_contains_multiple_resources_across_many_directories(tmp_repo: Path) -> None:
    namespace = _parse(tmp_repo, "namespace.yaml", "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: staging\n")
    deploy = _parse(
        tmp_repo,
        "apps/web/deployment.yaml",
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n  namespace: staging\nspec:\n  template:\n    spec:\n      containers:\n        - name: web\n          image: myapp:1.0\n",
    )
    config = _parse(
        tmp_repo, "config/app-config.yaml", "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: app-config\n  namespace: staging\n"
    )
    relationships = resolve_references(namespace + deploy + config)
    contains_edges = [r for r in relationships if r.relationship_type == "contains"]
    assert len(contains_edges) == 2
    assert {r.target for r in contains_edges} == {deploy[0].id, config[0].id}


# --- negative: no relationship when namespace not declared as a resource ---


def test_no_contains_relationship_when_namespace_object_not_declared(tmp_repo: Path) -> None:
    """Most repos never declare kind: Namespace explicitly (namespaces
    are frequently created out-of-band). This must produce zero
    fabricated relationships, not a guess."""
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
          namespace: staging
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    relationships = resolve_references(deploy)
    assert relationships == []


def test_no_contains_relationship_for_resource_with_no_namespace_field(tmp_repo: Path) -> None:
    namespace = _parse(tmp_repo, "namespace.yaml", "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: staging\n")
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  template:\n    spec:\n      containers:\n        - name: web\n          image: myapp:1.0\n",
    )
    relationships = resolve_references(namespace + deploy)
    assert relationships == []


def test_no_contains_relationship_for_mismatched_namespace_name(tmp_repo: Path) -> None:
    namespace = _parse(tmp_repo, "namespace.yaml", "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: production\n")
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n  namespace: staging\nspec:\n  template:\n    spec:\n      containers:\n        - name: web\n          image: myapp:1.0\n",
    )
    relationships = resolve_references(namespace + deploy)
    assert relationships == []


# --- boundary: the Namespace component itself is never "contained" --------


def test_namespace_never_contains_itself(tmp_repo: Path) -> None:
    namespace = _parse(tmp_repo, "namespace.yaml", "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: staging\n")
    relationships = resolve_references(namespace)
    assert relationships == []


# --- regression: existing namespace-scoped resolution unaffected -----------


def test_configmap_reference_resolution_unaffected_by_namespace_component(tmp_repo: Path) -> None:
    """Adding Namespace/contains support must not change the existing,
    separately-scoped ConfigMap resolution behavior at all."""
    namespace = _parse(tmp_repo, "namespace.yaml", "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: staging\n")
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
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
        tmp_repo, "config.yaml", "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: app-config\n  namespace: staging\n"
    )
    relationships = resolve_references(namespace + deploy + config)
    assert len(relationships) == 3  # namespace contains deploy, namespace contains config, deploy uses config
    assert {r.relationship_type for r in relationships} == {"contains", "uses"}


def test_deployment_still_recognized_without_namespace_object_present(tmp_repo: Path) -> None:
    """Exact regression for the pre-existing namespace-scoping fixture
    shape (6A.1): no Namespace object declared at all, resource-level
    namespace-scoped resolution still works exactly as before."""
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
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
        tmp_repo, "config.yaml", "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: app-config\n  namespace: staging\n"
    )
    relationships = resolve_references(deploy + config)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"
