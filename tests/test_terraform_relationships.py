"""Terraform: explicit depends_on and implicit interpolation references,
including across multiple .tf files (a completely normal way to
structure a real Terraform project)."""

from pathlib import Path

from app.parsers.terraform_parser import TerraformParser, resolve_references
from tests.conftest import write


def test_terraform_single_file_produces_reference_relationship(tmp_repo: Path) -> None:
    path = write(
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
    components = TerraformParser().parse(path, tmp_repo).components
    relationships = resolve_references(components)

    assert len(relationships) == 1
    assert relationships[0].relationship_type == "depends_on"


def test_terraform_resolves_references_across_files(tmp_repo: Path) -> None:
    """The realistic case: vpc.tf and ec2.tf are separate files in the
    same project, and ec2.tf's resource references one declared in
    vpc.tf. This can only be resolved after every file has been parsed."""
    vpc_path = write(tmp_repo, "vpc.tf", 'resource "aws_vpc" "main" {\n  cidr_block = "10.0.0.0/16"\n}\n')
    ec2_path = write(
        tmp_repo,
        "ec2.tf",
        """
        resource "aws_instance" "web" {
          ami = "ami-123"
          depends_on = [aws_vpc.main]
        }
        """,
    )

    components = []
    components += TerraformParser().parse(vpc_path, tmp_repo).components
    components += TerraformParser().parse(ec2_path, tmp_repo).components
    relationships = resolve_references(components)

    assert len(relationships) == 1
    source = next(c for c in components if c.id == relationships[0].source)
    target = next(c for c in components if c.id == relationships[0].target)
    assert source.name == "aws_instance.web"
    assert target.name == "aws_vpc.main"


def test_terraform_ignores_commented_out_and_non_resource_references(tmp_repo: Path) -> None:
    """A commented-out resource shouldn't be parsed at all, and var./
    local./data. references (which use the same dotted syntax for a
    different purpose) shouldn't be mistaken for resource references."""
    path = write(
        tmp_repo,
        "main.tf",
        """
        resource "aws_instance" "web" {
          ami = var.ami_id
          subnet_id = data.aws_subnet.existing.id
        }

        # resource "aws_instance" "old" {
        #   ami = "should-not-appear"
        # }
        """,
    )
    components = TerraformParser().parse(path, tmp_repo).components
    assert len(components) == 1
    assert components[0].metadata["referenced_identifiers"] == []
    relationships = resolve_references(components)
    assert relationships == []
