"""Phase 6D.3: Terraform root-level output components.

`output "name" { value = ... }` blocks become one "terraform_output"
component per block. Unlike locals/data sources (always pure targets),
an output's `value` expression IS scanned for references, producing
`output --USES--> {resource | module_call | local_value | data_source}`
relationships - the same directory-scoped mechanism, extended to a new
source rather than a new target lookup. Required zero changes to
resolve_references() itself, since its main loop already iterates every
Terraform component's referenced_* fields generically.

Not the deferred Tier B problem: this is a root-level output referencing
something in its OWN configuration, not resolving module.x.output_name
through a module boundary into a child module's internals.
"""

from pathlib import Path

from app.parsers.terraform_parser import TerraformParser, resolve_references
from tests.conftest import write


def _parse(tmp_repo: Path, filename: str, terraform_text: str):
    path = write(tmp_repo, filename, terraform_text)
    return TerraformParser().parse(path, tmp_repo).components


# --- output detection ----------------------------------------------------------


def test_output_block_detected_as_component(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        resource "aws_vpc" "main" {
          cidr_block = "10.0.0.0/16"
        }
        output "vpc_id" {
          value = aws_vpc.main.id
        }
        """,
    )
    outputs = [c for c in components if c.type == "terraform_output"]
    assert len(outputs) == 1
    assert outputs[0].metadata["output_name"] == "vpc_id"
    assert outputs[0].id == "terraform:main.tf:output.vpc_id"
    assert outputs[0].name == "output.vpc_id"


def test_multiple_outputs_detected(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        resource "aws_vpc" "main" {
          cidr_block = "10.0.0.0/16"
        }
        output "vpc_id" {
          value = aws_vpc.main.id
        }
        output "vpc_cidr" {
          value = aws_vpc.main.cidr_block
        }
        """,
    )
    outputs = [c for c in components if c.type == "terraform_output"]
    assert {c.metadata["output_name"] for c in outputs} == {"vpc_id", "vpc_cidr"}


# --- output -> resource -------------------------------------------------------


def test_output_referencing_resource_resolves(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        resource "aws_vpc" "main" {
          cidr_block = "10.0.0.0/16"
        }
        output "vpc_id" {
          value = aws_vpc.main.id
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"

    output = next(c for c in components if c.type == "terraform_output")
    resource = next(c for c in components if c.name == "aws_vpc.main")
    assert relationships[0].source == output.id
    assert relationships[0].target == resource.id


# --- output -> module call ----------------------------------------------------


def test_output_referencing_module_call_resolves(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        module "network" {
          source = "./modules/network"
        }
        output "vpc_id" {
          value = module.network.vpc_id
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"

    output = next(c for c in components if c.type == "terraform_output")
    module_call = next(c for c in components if c.type == "terraform_module_call")
    assert relationships[0].source == output.id
    assert relationships[0].target == module_call.id


# --- output -> local value ----------------------------------------------------


def test_output_referencing_local_resolves(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        locals {
          region = "us-east-1"
        }
        output "region" {
          value = local.region
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"

    output = next(c for c in components if c.type == "terraform_output")
    local = next(c for c in components if c.type == "terraform_local_value")
    assert relationships[0].source == output.id
    assert relationships[0].target == local.id


# --- output -> data source ----------------------------------------------------


def test_output_referencing_data_source_resolves(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        data "aws_ami" "ubuntu" {
          most_recent = true
        }
        output "ami_id" {
          value = data.aws_ami.ubuntu.id
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"

    output = next(c for c in components if c.type == "terraform_output")
    data_source = next(c for c in components if c.type == "terraform_data_source")
    assert relationships[0].source == output.id
    assert relationships[0].target == data_source.id


# --- output referencing multiple kinds at once -------------------------------


def test_output_can_reference_multiple_target_kinds(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        resource "aws_vpc" "main" {
          cidr_block = "10.0.0.0/16"
        }
        module "network" {
          source = "./modules/network"
        }
        locals {
          region = "us-east-1"
        }
        output "summary" {
          value = "${aws_vpc.main.id} ${module.network.vpc_id} ${local.region}"
        }
        """,
    )
    relationships = resolve_references(components)
    output = next(c for c in components if c.type == "terraform_output")
    output_relationships = [r for r in relationships if r.source == output.id]
    assert len(output_relationships) == 3
    assert {r.relationship_type for r in output_relationships} == {"uses"}


# --- directory scoping ----------------------------------------------------------


def test_output_resolves_within_same_directory(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "environments/staging/main.tf",
        """
        resource "aws_vpc" "main" {
          cidr_block = "10.0.0.0/16"
        }
        output "vpc_id" {
          value = aws_vpc.main.id
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1


def test_output_does_not_resolve_across_different_directories(tmp_repo: Path) -> None:
    staging = _parse(
        tmp_repo, "environments/staging/vpc.tf", 'resource "aws_vpc" "main" {\n  cidr_block = "10.0.0.0/16"\n}\n'
    )
    production = _parse(
        tmp_repo, "environments/production/outputs.tf", 'output "vpc_id" {\n  value = aws_vpc.main.id\n}\n'
    )
    relationships = resolve_references(staging + production)
    assert relationships == []


# --- missing target --------------------------------------------------------------


def test_output_referencing_undeclared_resource_produces_no_relationship(tmp_repo: Path) -> None:
    components = _parse(tmp_repo, "main.tf", 'output "vpc_id" {\n  value = aws_vpc.main.id\n}\n')
    assert resolve_references(components) == []


# --- regression: existing resource/module/local/data-source behavior unaffected --


def test_existing_resource_to_resource_relationship_unaffected_by_outputs(tmp_repo: Path) -> None:
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
        output "vpc_id" {
          value = aws_vpc.main.id
        }
        """,
    )
    relationships = resolve_references(components)
    depends_on_edges = [r for r in relationships if r.relationship_type == "depends_on"]
    assert len(depends_on_edges) == 1

    subnet = next(c for c in components if c.name == "aws_subnet.public")
    vpc = next(c for c in components if c.name == "aws_vpc.main")
    assert depends_on_edges[0].source == subnet.id
    assert depends_on_edges[0].target == vpc.id


def test_output_never_becomes_a_valid_resolution_target(tmp_repo: Path) -> None:
    """Real Terraform never lets a reference within the same
    configuration resolve to an output - only a parent module can
    reference a child's output (module.x.output_name), which stays the
    deferred Tier B case. Confirms outputs are source-only here."""
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        output "vpc_id" {
          value = "10.0.0.0/16"
        }
        resource "aws_instance" "web" {
          ami = "ami-123"
        }
        """,
    )
    relationships = resolve_references(components)
    assert relationships == []


def test_output_reference_has_parsed_provenance(tmp_repo: Path) -> None:
    from app.graph.engine import GraphEngine
    from app.models.ikm import InfrastructureModel

    components = _parse(
        tmp_repo,
        "main.tf",
        """
        resource "aws_vpc" "main" {
          cidr_block = "10.0.0.0/16"
        }
        output "vpc_id" {
          value = aws_vpc.main.id
        }
        """,
    )
    relationships = resolve_references(components)
    model = InfrastructureModel(components=components, relationships=relationships)
    engine = GraphEngine.from_infrastructure_model(model, infer=True)
    edge_model = engine.to_model()
    output_edge = next(e for e in edge_model.edges if e.edge_type == "uses")
    assert output_edge.metadata["origin"] == "parsed"
