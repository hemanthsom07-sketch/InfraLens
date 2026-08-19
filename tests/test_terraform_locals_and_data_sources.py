"""Phase 6C.4/6C.5: Terraform locals and data sources.

6C.4 - `locals { key = expr, ... }` blocks become one
       "terraform_local_value" component per key. A resource or module
       call referencing `local.key` resolves against it, directory-scoped
       exactly like module calls (6B). Deliberately NOT resolving what a
       local's own expression references (no module-output-style
       recursive resolution).
6C.5 - `data "type" "name" { ... }` blocks become one
       "terraform_data_source" component. A resource or module call
       referencing `data.type.name` resolves against it, same
       directory-scoped mechanism. Data sources are never treated as
       ordinary resources.
"""

from pathlib import Path

from app.parsers.terraform_parser import TerraformParser, resolve_references
from tests.conftest import write


def _parse(tmp_repo: Path, filename: str, terraform_text: str):
    path = write(tmp_repo, filename, terraform_text)
    return TerraformParser().parse(path, tmp_repo).components


# --- 6C.4: local detection ----------------------------------------------------


def test_locals_block_detected_as_components(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        locals {
          region = "us-east-1"
          environment = "staging"
        }
        """,
    )
    locals_found = [c for c in components if c.type == "terraform_local_value"]
    assert {c.metadata["local_name"] for c in locals_found} == {"region", "environment"}


def test_local_component_id_and_name(tmp_repo: Path) -> None:
    components = _parse(tmp_repo, "main.tf", 'locals {\n  region = "us-east-1"\n}\n')
    local = components[0]
    assert local.id == "terraform:main.tf:local.region"
    assert local.name == "local.region"
    assert local.technology == "terraform"


def test_multiple_locals_in_one_block(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        locals {
          a = "1"
          b = "2"
          c = "3"
        }
        """,
    )
    assert len(components) == 3
    assert {c.metadata["local_name"] for c in components} == {"a", "b", "c"}


# --- 6C.4: resource -> local resolution ---------------------------------------


def test_resource_referencing_local_resolves(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        locals {
          region = "us-east-1"
        }
        resource "aws_instance" "web" {
          availability_zone = local.region
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"

    local = next(c for c in components if c.type == "terraform_local_value")
    resource = next(c for c in components if c.name == "aws_instance.web")
    assert relationships[0].source == resource.id
    assert relationships[0].target == local.id


def test_resource_referencing_undeclared_local_produces_no_relationship(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        resource "aws_instance" "web" {
          availability_zone = local.region
        }
        """,
    )
    assert resolve_references(components) == []


def test_local_value_expression_is_not_resolved_into_its_own_references(tmp_repo: Path) -> None:
    """The explicit scope boundary: a local's own body referencing a
    resource must NOT produce a local->resource relationship (that would
    be the same "resolve the internals" problem module outputs have, and
    stays out of scope)."""
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        resource "aws_subnet" "private" {
          cidr_block = "10.0.1.0/24"
        }
        locals {
          subnet_id = aws_subnet.private.id
        }
        """,
    )
    relationships = resolve_references(components)
    local = next(c for c in components if c.type == "terraform_local_value")
    assert all(r.source != local.id for r in relationships)


# --- 6C.4: directory scoping ---------------------------------------------------


def test_local_resolves_within_same_directory(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "environments/staging/main.tf",
        """
        locals {
          region = "us-east-1"
        }
        resource "aws_instance" "web" {
          availability_zone = local.region
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1


def test_local_does_not_resolve_across_different_directories(tmp_repo: Path) -> None:
    staging = _parse(tmp_repo, "environments/staging/locals.tf", 'locals {\n  region = "us-east-1"\n}\n')
    production = _parse(
        tmp_repo,
        "environments/production/main.tf",
        """
        resource "aws_instance" "web" {
          availability_zone = local.region
        }
        """,
    )
    relationships = resolve_references(staging + production)
    assert relationships == []


# --- 6C.4: malformed block -----------------------------------------------------


def test_empty_locals_block_produces_no_components(tmp_repo: Path) -> None:
    components = _parse(tmp_repo, "main.tf", "locals {\n}\n")
    assert [c for c in components if c.type == "terraform_local_value"] == []


# --- 6C.4: regression - existing resource-to-resource unaffected -----------


def test_existing_resource_to_resource_relationship_unaffected_by_locals(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        resource "aws_vpc" "main" {
          cidr_block = "10.0.0.0/16"
        }
        resource "aws_subnet" "public" {
          vpc_id = aws_vpc.main.id
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "depends_on"


def test_local_reference_has_parsed_provenance(tmp_repo: Path) -> None:
    from app.graph.engine import GraphEngine
    from app.models.ikm import InfrastructureModel

    components = _parse(
        tmp_repo,
        "main.tf",
        """
        locals {
          region = "us-east-1"
        }
        resource "aws_instance" "web" {
          availability_zone = local.region
        }
        """,
    )
    relationships = resolve_references(components)
    model = InfrastructureModel(components=components, relationships=relationships)
    engine = GraphEngine.from_infrastructure_model(model, infer=True)
    edge_model = engine.to_model()
    local_edge = next(e for e in edge_model.edges if e.edge_type == "uses")
    assert local_edge.metadata["origin"] == "parsed"


# --- 6C.5: data source detection -----------------------------------------------


def test_data_source_block_detected_as_component(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        data "aws_ami" "ubuntu" {
          most_recent = true
        }
        """,
    )
    data_sources = [c for c in components if c.type == "terraform_data_source"]
    assert len(data_sources) == 1
    assert data_sources[0].metadata["data_type"] == "aws_ami"
    assert data_sources[0].metadata["data_name"] == "ubuntu"
    assert data_sources[0].id == "terraform:main.tf:data.aws_ami.ubuntu"
    assert data_sources[0].name == "data.aws_ami.ubuntu"


def test_multiple_data_sources_detected(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        data "aws_ami" "ubuntu" {
          most_recent = true
        }
        data "aws_subnet" "existing" {
          id = "subnet-123"
        }
        """,
    )
    data_sources = [c for c in components if c.type == "terraform_data_source"]
    assert len(data_sources) == 2


# --- 6C.5: resource -> data source resolution --------------------------------


def test_resource_referencing_data_source_resolves(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        data "aws_ami" "ubuntu" {
          most_recent = true
        }
        resource "aws_instance" "web" {
          ami = data.aws_ami.ubuntu.id
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"

    data_source = next(c for c in components if c.type == "terraform_data_source")
    resource = next(c for c in components if c.name == "aws_instance.web")
    assert relationships[0].source == resource.id
    assert relationships[0].target == data_source.id


def test_resource_referencing_undeclared_data_source_produces_no_relationship(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        resource "aws_instance" "web" {
          ami = data.aws_ami.ubuntu.id
        }
        """,
    )
    assert resolve_references(components) == []


# --- 6C.5: directory scoping ---------------------------------------------------


def test_data_source_resolves_within_same_directory(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "environments/staging/main.tf",
        """
        data "aws_ami" "ubuntu" {
          most_recent = true
        }
        resource "aws_instance" "web" {
          ami = data.aws_ami.ubuntu.id
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1


def test_data_source_does_not_resolve_across_different_directories(tmp_repo: Path) -> None:
    staging = _parse(
        tmp_repo, "environments/staging/data.tf", 'data "aws_ami" "ubuntu" {\n  most_recent = true\n}\n'
    )
    production = _parse(
        tmp_repo,
        "environments/production/main.tf",
        """
        resource "aws_instance" "web" {
          ami = data.aws_ami.ubuntu.id
        }
        """,
    )
    relationships = resolve_references(staging + production)
    assert relationships == []


# --- 6C.5: data sources are never treated as ordinary resources ------------


def test_data_source_never_matches_a_resource_lookup(tmp_repo: Path) -> None:
    """A data source and a resource can share a type/name-shaped
    identity (e.g. "aws_ami.ubuntu") without ever being confused -
    depends_on referencing "aws_ami.ubuntu" must not accidentally
    resolve against a data source of the same type/name."""
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        data "aws_ami" "ubuntu" {
          most_recent = true
        }
        resource "aws_instance" "web" {
          ami = data.aws_ami.ubuntu.id
          depends_on = [aws_ami.ubuntu]
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"


# --- 6C.5: regression - existing behavior unaffected -------------------------


def test_existing_data_reference_still_excluded_from_referenced_identifiers(tmp_repo: Path) -> None:
    """Exact regression for the pre-existing
    test_terraform_ignores_commented_out_and_non_resource_references
    fixture shape: a data.* reference with no matching data block
    declared must still produce zero relationships and must never
    appear in referenced_identifiers."""
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        resource "aws_instance" "web" {
          ami = var.ami_id
          subnet_id = data.aws_subnet.existing.id
        }
        """,
    )
    assert len(components) == 1
    assert components[0].metadata["referenced_identifiers"] == []
    assert resolve_references(components) == []


def test_data_source_reference_has_parsed_provenance(tmp_repo: Path) -> None:
    from app.graph.engine import GraphEngine
    from app.models.ikm import InfrastructureModel

    components = _parse(
        tmp_repo,
        "main.tf",
        """
        data "aws_ami" "ubuntu" {
          most_recent = true
        }
        resource "aws_instance" "web" {
          ami = data.aws_ami.ubuntu.id
        }
        """,
    )
    relationships = resolve_references(components)
    model = InfrastructureModel(components=components, relationships=relationships)
    engine = GraphEngine.from_infrastructure_model(model, infer=True)
    edge_model = engine.to_model()
    data_edge = next(e for e in edge_model.edges if e.edge_type == "uses")
    assert data_edge.metadata["origin"] == "parsed"
