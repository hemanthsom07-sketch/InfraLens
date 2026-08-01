"""End-to-end: Components -> Relationships -> GraphBuilder -> Nodes + Edges.

Exercises the actual code path api/v1/analyze.py uses (scan_repository ->
build_infrastructure_model -> build_graph), against a small multi-technology
fixture written to disk, rather than hand-built Component objects — this
is what would have caught the original "relationships always empty" bug,
since it runs the real dispatch + resolution pipeline end to end.
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


def test_compose_repository_produces_relationships_and_graph_edges(tmp_repo: Path) -> None:
    write(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          backend:
            build: .
            depends_on:
              - database
          database:
            image: postgres:15
        """,
    )
    ikm, engine = _build(tmp_repo)
    graph_model = engine.to_model()

    assert len(ikm.relationships) > 0
    assert graph_model.metadata["node_count"] > 0
    assert graph_model.metadata["edge_count"] > 0


def test_kubernetes_repository_produces_relationships_and_graph_edges(tmp_repo: Path) -> None:
    write(
        tmp_repo,
        "k8s/app.yaml",
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
                  envFrom:
                    - configMapRef:
                        name: backend-config
        ---
        apiVersion: v1
        kind: ConfigMap
        metadata:
          name: backend-config
        ---
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
    ikm, engine = _build(tmp_repo)
    graph_model = engine.to_model()

    assert len(ikm.relationships) > 0  # the Deployment -> ConfigMap "uses" relationship
    assert graph_model.metadata["node_count"] > 0
    assert graph_model.metadata["edge_count"] > 0
    # graph edges should be >= IKM relationships: every parsed relationship
    # becomes an edge, PLUS the Service -> Deployment edge Phase 4's
    # existing selector-matching inference rule adds on top.
    assert graph_model.metadata["edge_count"] >= len(ikm.relationships)


def test_terraform_repository_produces_relationships_and_graph_edges(tmp_repo: Path) -> None:
    write(
        tmp_repo,
        "terraform/main.tf",
        """
        resource "aws_vpc" "main" {
          cidr_block = "10.0.0.0/16"
        }
        resource "aws_subnet" "public" {
          vpc_id = aws_vpc.main.id
        }
        """,
    )
    ikm, engine = _build(tmp_repo)
    graph_model = engine.to_model()

    assert len(ikm.relationships) > 0
    assert graph_model.metadata["node_count"] > 0
    assert graph_model.metadata["edge_count"] > 0


def test_every_ikm_relationship_has_a_corresponding_graph_edge(tmp_repo: Path) -> None:
    """The specific chain the bug report asked to verify: every
    Relationship GraphBuilder receives becomes a graph Edge with matching
    source/target/type, tagged origin="parsed" (as opposed to the
    separately-tagged origin="inferred" edges Phase 4's inference rules add)."""
    write(
        tmp_repo,
        "docker-compose.yml",
        """
        services:
          backend:
            build: .
            networks: [app-net]
            depends_on:
              - database
          database:
            image: postgres:15
            networks: [app-net]
            volumes:
              - db_data:/var/lib/postgresql/data
        """,
    )
    ikm, engine = _build(tmp_repo)
    graph_model = engine.to_model()

    parsed_edges = [e for e in graph_model.edges if e.metadata.get("origin") == "parsed"]
    assert len(parsed_edges) == len(ikm.relationships)

    parsed_pairs = {(e.source, e.target, e.edge_type) for e in parsed_edges}
    ikm_pairs = {(r.source, r.target, r.relationship_type) for r in ikm.relationships}
    assert parsed_pairs == ikm_pairs


def test_repository_with_no_infrastructure_files_has_empty_graph(tmp_repo: Path) -> None:
    """The negative case: a repo with nothing to detect should produce an
    empty (not broken) graph — zero nodes, zero edges, no error."""
    write(tmp_repo, "README.md", "# Just a readme, no infrastructure here.\n")
    ikm, engine = _build(tmp_repo)
    graph_model = engine.to_model()

    assert ikm.relationships == []
    assert graph_model.metadata["node_count"] == 0
    assert graph_model.metadata["edge_count"] == 0
