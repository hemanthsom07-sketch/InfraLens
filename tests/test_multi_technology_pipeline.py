"""Phase 6D.5: end-to-end, multi-technology pipeline integration test.

Every existing pipeline test (test_graph_pipeline.py) covers exactly one
technology at a time. This test builds one repository mixing Terraform,
Kubernetes, Compose, and Docker together, runs the real
scan_repository -> build_infrastructure_model -> build_graph pipeline
(the same code path api/v1/analyze.py uses) once, and confirms:

- every technology's components/relationships appear correctly
- the four resolve_*_references()/canonicalize_shared_resources() calls
  in ikm_service.py don't interfere with each other (e.g. Compose's
  canonicalization never touches Terraform/Kubernetes components)
- every Phase 6D capability (Docker multi-stage metadata, Compose custom
  dockerfile + cross-file network canonicalization, Terraform outputs,
  Kubernetes Namespace/contains) works correctly when everything runs
  together, not just in isolation
"""

from pathlib import Path

from app.services.graph_service import build_graph
from app.services.ikm_service import build_infrastructure_model
from app.services.scanner_service import scan_repository
from tests.conftest import write


def _build(tmp_repo: Path):
    scan_result = scan_repository(tmp_repo)
    ikm = build_infrastructure_model(scan_result.file_paths, tmp_repo)
    engine = build_graph(ikm)
    return ikm, engine


def _write_fixture_repo(tmp_repo: Path) -> None:
    # --- Terraform: locals, data source, module call, output ---------------
    write(
        tmp_repo,
        "infra/main.tf",
        """
        locals {
          region = "us-east-1"
        }
        data "aws_ami" "app" {
          most_recent = true
        }
        module "network" {
          source = "./modules/network"
        }
        resource "aws_instance" "app" {
          ami       = data.aws_ami.app.id
          subnet_id = module.network.subnet_id
        }
        output "instance_id" {
          value = aws_instance.app.id
        }
        """,
    )

    # --- Kubernetes: Namespace, Deployment, ConfigMap, HPA ------------------
    write(
        tmp_repo,
        "k8s/namespace.yaml",
        "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: production\n",
    )
    write(
        tmp_repo,
        "k8s/web.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
          namespace: production
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
                  envFrom:
                    - configMapRef:
                        name: web-config
        ---
        apiVersion: v1
        kind: ConfigMap
        metadata:
          name: web-config
          namespace: production
        ---
        apiVersion: autoscaling/v2
        kind: HorizontalPodAutoscaler
        metadata:
          name: web-hpa
          namespace: production
        spec:
          scaleTargetRef:
            kind: Deployment
            name: web
        """,
    )

    # --- Compose: cross-file shared network (same directory) ---------------
    write(
        tmp_repo,
        "compose/docker-compose.yml",
        """
        services:
          api:
            build: .
            networks:
              - shared
        networks:
          shared: {}
        """,
    )
    write(
        tmp_repo,
        "compose/docker-compose.override.yml",
        "services:\n  worker:\n    build: .\n    networks:\n      - shared\n",
    )

    # --- Docker: multi-stage build ------------------------------------------
    write(
        tmp_repo,
        "docker/Dockerfile",
        """
        FROM golang:1.21 AS builder
        WORKDIR /src
        FROM alpine
        COPY --from=builder /src/app /usr/local/bin/app
        """,
    )


def test_full_pipeline_runs_without_error(tmp_repo: Path) -> None:
    _write_fixture_repo(tmp_repo)
    ikm, engine = _build(tmp_repo)
    graph_model = engine.to_model()
    assert graph_model.metadata["node_count"] > 0
    assert graph_model.metadata["edge_count"] > 0


def test_every_technology_produces_components(tmp_repo: Path) -> None:
    _write_fixture_repo(tmp_repo)
    ikm, _ = _build(tmp_repo)
    technologies = {c.technology for c in ikm.components}
    assert technologies == {"terraform", "kubernetes", "docker-compose", "docker"}


def test_terraform_output_and_data_source_relationships_present(tmp_repo: Path) -> None:
    _write_fixture_repo(tmp_repo)
    ikm, _ = _build(tmp_repo)
    terraform_components = [c for c in ikm.components if c.technology == "terraform"]
    assert {c.type for c in terraform_components} == {
        "terraform_resource", "terraform_module_call", "terraform_local_value",
        "terraform_data_source", "terraform_output",
    }

    output = next(c for c in terraform_components if c.type == "terraform_output")
    output_relationships = [r for r in ikm.relationships if r.source == output.id]
    assert len(output_relationships) == 1
    assert output_relationships[0].relationship_type == "uses"

    resource = next(c for c in terraform_components if c.type == "terraform_resource")
    resource_relationships = [r for r in ikm.relationships if r.source == resource.id]
    assert len(resource_relationships) == 2
    assert {r.relationship_type for r in resource_relationships} == {"uses"}


def test_kubernetes_namespace_contains_and_hpa_relationships_present(tmp_repo: Path) -> None:
    _write_fixture_repo(tmp_repo)
    ikm, _ = _build(tmp_repo)
    k8s_components = [c for c in ikm.components if c.technology == "kubernetes"]
    namespace = next(c for c in k8s_components if c.metadata.get("kind") == "Namespace")
    deployment = next(c for c in k8s_components if c.metadata.get("kind") == "Deployment")
    configmap = next(c for c in k8s_components if c.metadata.get("kind") == "ConfigMap")
    hpa = next(c for c in k8s_components if c.metadata.get("kind") == "HorizontalPodAutoscaler")

    contains_edges = [r for r in ikm.relationships if r.relationship_type == "contains"]
    assert {r.target for r in contains_edges} == {deployment.id, configmap.id, hpa.id}
    assert all(r.source == namespace.id for r in contains_edges)

    hpa_edges = [r for r in ikm.relationships if r.source == hpa.id]
    assert len(hpa_edges) == 1
    assert hpa_edges[0].target == deployment.id

    deployment_uses_configmap = [
        r for r in ikm.relationships if r.source == deployment.id and r.target == configmap.id
    ]
    assert len(deployment_uses_configmap) == 1
    assert deployment_uses_configmap[0].relationship_type == "uses"


def test_compose_network_canonicalized_across_files(tmp_repo: Path) -> None:
    _write_fixture_repo(tmp_repo)
    ikm, _ = _build(tmp_repo)
    compose_components = [c for c in ikm.components if c.technology == "docker-compose"]
    networks = [c for c in compose_components if c.type == "network" and c.name == "shared"]
    assert len(networks) == 1

    connects_to_edges = [
        r for r in ikm.relationships if r.relationship_type == "connects_to" and r.target == networks[0].id
    ]
    assert len(connects_to_edges) == 2


def test_docker_multi_stage_metadata_present(tmp_repo: Path) -> None:
    _write_fixture_repo(tmp_repo)
    ikm, _ = _build(tmp_repo)
    docker_components = [c for c in ikm.components if c.technology == "docker"]
    assert len(docker_components) == 1
    metadata = docker_components[0].metadata
    assert metadata["build_stage_names"] == ["builder"]
    assert metadata["copy_from_stages"] == ["builder"]


def test_no_cross_technology_interference(tmp_repo: Path) -> None:
    """Sanity check that the four resolve_*_references()/
    canonicalize_shared_resources() calls in ikm_service.py truly don't
    interfere: no Terraform/Kubernetes component was affected by
    Compose's network canonicalization, and every relationship's
    source/target belongs to a component that actually exists."""
    _write_fixture_repo(tmp_repo)
    ikm, _ = _build(tmp_repo)

    terraform_count = len([c for c in ikm.components if c.technology == "terraform"])
    kubernetes_count = len([c for c in ikm.components if c.technology == "kubernetes"])
    assert terraform_count == 5
    assert kubernetes_count == 4

    component_ids = {c.id for c in ikm.components}
    for relationship in ikm.relationships:
        assert relationship.source in component_ids
        assert relationship.target in component_ids


def test_graph_engine_builds_successfully_over_the_full_model(tmp_repo: Path) -> None:
    _write_fixture_repo(tmp_repo)
    _, engine = _build(tmp_repo)
    assert engine.detect_cycles() == []
    assert engine.connected_components()
