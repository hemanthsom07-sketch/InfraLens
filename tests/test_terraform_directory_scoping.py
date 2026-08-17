"""Phase 6A.7: Terraform directory-scoped reference resolution.

Before this change, resolve_references() used a flat, repo-wide
"type.name" -> Component map, so same-named resources in different
environment directories (a near-universal environments/staging/,
environments/production/ layout) could collide. Resolution is now scoped
to same-directory .tf files only.
"""

from pathlib import Path

from app.parsers.terraform_parser import TerraformParser, resolve_references
from tests.conftest import write


def _parse(tmp_repo: Path, filename: str, terraform_text: str):
    path = write(tmp_repo, filename, terraform_text)
    return TerraformParser().parse(path, tmp_repo).components


# --- negative: different directories, same type.name -----------------------


def test_reference_does_not_resolve_across_different_directories(tmp_repo: Path) -> None:
    staging = _parse(
        tmp_repo,
        "environments/staging/main.tf",
        """
        resource "aws_vpc" "main" {
          cidr_block = "10.0.0.0/16"
        }
        resource "aws_subnet" "public" {
          vpc_id = aws_vpc.main.id
        }
        """,
    )
    # Reference-only component in a DIFFERENT directory, pointing at a
    # same-named "aws_vpc.main" that only exists in staging/ above.
    production = _parse(
        tmp_repo,
        "environments/production/subnet.tf",
        """
        resource "aws_subnet" "public" {
          vpc_id = aws_vpc.main.id
        }
        """,
    )
    relationships = resolve_references(staging + production)

    # Only staging's own internal reference should resolve; production's
    # same-named reference must NOT cross-resolve against staging's VPC.
    assert len(relationships) == 1
    source = next(c for c in staging + production if c.id == relationships[0].source)
    assert source.metadata["source_file"] == "environments/staging/main.tf"


def test_same_named_resources_in_different_directories_do_not_collide(tmp_repo: Path) -> None:
    """The exact false-positive scenario from the design phase: both
    staging and production declare their OWN aws_vpc.main and their OWN
    aws_subnet.public referencing it — each must resolve to its OWN
    directory's VPC, never the other's."""
    staging = _parse(
        tmp_repo,
        "environments/staging/main.tf",
        """
        resource "aws_vpc" "main" {
          cidr_block = "10.0.0.0/16"
        }
        resource "aws_subnet" "public" {
          vpc_id = aws_vpc.main.id
        }
        """,
    )
    production = _parse(
        tmp_repo,
        "environments/production/main.tf",
        """
        resource "aws_vpc" "main" {
          cidr_block = "10.1.0.0/16"
        }
        resource "aws_subnet" "public" {
          vpc_id = aws_vpc.main.id
        }
        """,
    )
    relationships = resolve_references(staging + production)
    assert len(relationships) == 2

    by_source_directory = {}
    for relationship in relationships:
        source = next(c for c in staging + production if c.id == relationship.source)
        target = next(c for c in staging + production if c.id == relationship.target)
        source_dir = source.metadata["source_file"].rsplit("/", 1)[0]
        target_dir = target.metadata["source_file"].rsplit("/", 1)[0]
        by_source_directory[source_dir] = target_dir

    assert by_source_directory["environments/staging"] == "environments/staging"
    assert by_source_directory["environments/production"] == "environments/production"


# --- positive: same directory, multiple files (the common real layout) -----


def test_reference_still_resolves_across_files_in_the_same_directory(tmp_repo: Path) -> None:
    """Regression: the realistic, dominant Terraform layout — multiple
    .tf files in ONE directory forming one conceptual root module — must
    keep resolving exactly as before."""
    vpc = _parse(tmp_repo, "vpc.tf", 'resource "aws_vpc" "main" {\n  cidr_block = "10.0.0.0/16"\n}\n')
    ec2 = _parse(
        tmp_repo,
        "ec2.tf",
        """
        resource "aws_instance" "web" {
          ami = "ami-123"
          depends_on = [aws_vpc.main]
        }
        """,
    )
    relationships = resolve_references(vpc + ec2)
    assert len(relationships) == 1
    source = next(c for c in vpc + ec2 if c.id == relationships[0].source)
    target = next(c for c in vpc + ec2 if c.id == relationships[0].target)
    assert source.name == "aws_instance.web"
    assert target.name == "aws_vpc.main"


def test_reference_resolves_within_the_same_nested_directory(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "environments/staging/main.tf",
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


# --- single-file regression (unaffected by directory scoping) --------------


def test_single_file_reference_still_resolves(tmp_repo: Path) -> None:
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
