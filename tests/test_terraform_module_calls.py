"""Phase 6B: Terraform module call detection (6B.1), coarse module-level
relationships (6B.2/6B.2b), and local module source normalization
(6B.3). Also confirms module-call components participate naturally in
graph construction and component discovery (6B.4) with no production
changes needed there.

Deliberately NOT covered here (explicitly out of scope for Phase 6B):
resolving module.x.output_name into the specific internal resource that
produces it, recursive child-module parsing, or registry/git module
downloading.
"""

from pathlib import Path

from app.graph.engine import GraphEngine
from app.parsers.terraform_parser import TerraformParser, resolve_references
from app.services import component_lookup_service
from app.models.ikm import Component, InfrastructureModel, Relationship
from tests.conftest import write


def _parse(tmp_repo: Path, filename: str, terraform_text: str):
    path = write(tmp_repo, filename, terraform_text)
    return TerraformParser().parse(path, tmp_repo).components


# --- 1/2. Basic module block detection + name captured ----------------------


def test_module_block_is_detected_as_a_component(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        module "network" {
          source = "./modules/network"
        }
        """,
    )
    module_calls = [c for c in components if c.type == "terraform_module_call"]
    assert len(module_calls) == 1
    assert module_calls[0].metadata["module_name"] == "network"


def test_module_block_component_id_and_name(tmp_repo: Path) -> None:
    components = _parse(tmp_repo, "main.tf", 'module "network" {\n  source = "./modules/network"\n}\n')
    module_call = components[0]
    assert module_call.id == "terraform:main.tf:module.network"
    assert module_call.name == "module.network"
    assert module_call.technology == "terraform"


def test_multiline_module_block_is_detected(tmp_repo: Path) -> None:
    """Equivalent valid multiline formatting, including a nested block
    (providers) inside the module call — _extract_block_body's balanced-
    brace counting must handle this correctly, same as it already does
    for resource blocks."""
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        module "network" {
          source = "./modules/network"

          providers = {
            aws = aws.primary
          }

          cidr_block = "10.0.0.0/16"
        }
        """,
    )
    module_calls = [c for c in components if c.type == "terraform_module_call"]
    assert len(module_calls) == 1
    assert module_calls[0].metadata["module_source"] == "./modules/network"


# --- 3/4/5. Local source: captured, normalized, is_local_source=True -------


def test_local_source_captured_and_flagged(tmp_repo: Path) -> None:
    components = _parse(tmp_repo, "main.tf", 'module "network" {\n  source = "./modules/network"\n}\n')
    metadata = components[0].metadata
    assert metadata["module_source"] == "./modules/network"
    assert metadata["is_local_source"] is True


def test_local_source_normalized_relative_to_calling_file(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo, "environments/staging/main.tf", 'module "network" {\n  source = "./modules/network"\n}\n'
    )
    assert components[0].metadata["resolved_source_directory"] == "environments/staging/modules/network"


def test_local_source_with_parent_directory_traversal_normalized(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo, "environments/staging/main.tf", 'module "shared" {\n  source = "../../shared/network"\n}\n'
    )
    assert components[0].metadata["resolved_source_directory"] == "shared/network"


def test_local_source_escaping_repo_root_omits_resolved_directory(tmp_repo: Path) -> None:
    """A local source that normalizes to outside the repo root (as far as
    can be told from the path string alone) must not produce a
    misleading repo-relative path — omit the key rather than guess."""
    components = _parse(tmp_repo, "main.tf", 'module "odd" {\n  source = "../../outside"\n}\n')
    assert "resolved_source_directory" not in components[0].metadata
    # is_local_source is still True — it IS a local relative path
    # syntactically, resolution just couldn't produce an in-repo result.
    assert components[0].metadata["is_local_source"] is True


# --- 6/7. Registry source: captured, is_local_source=False ------------------


def test_registry_source_captured_and_not_flagged_local(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo, "main.tf", 'module "vpc" {\n  source = "terraform-aws-modules/vpc/aws"\n}\n'
    )
    metadata = components[0].metadata
    assert metadata["module_source"] == "terraform-aws-modules/vpc/aws"
    assert metadata["is_local_source"] is False
    assert "resolved_source_directory" not in metadata


# --- 8/9. Git source: captured, is_local_source=False -----------------------


def test_git_source_captured_and_not_flagged_local(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        'module "vpc" {\n  source = "git::https://github.com/example/terraform-modules.git//vpc"\n}\n',
    )
    metadata = components[0].metadata
    assert metadata["module_source"] == "git::https://github.com/example/terraform-modules.git//vpc"
    assert metadata["is_local_source"] is False
    assert "resolved_source_directory" not in metadata


# --- 10. Missing source handled safely --------------------------------------


def test_module_block_with_no_source_does_not_crash_and_omits_source_keys(tmp_repo: Path) -> None:
    components = _parse(tmp_repo, "main.tf", 'module "incomplete" {\n  some_var = "x"\n}\n')
    module_calls = [c for c in components if c.type == "terraform_module_call"]
    assert len(module_calls) == 1
    metadata = module_calls[0].metadata
    assert metadata["module_name"] == "incomplete"
    assert "module_source" not in metadata
    assert "is_local_source" not in metadata
    assert "resolved_source_directory" not in metadata


# --- 11/12/13/14. resource -> module_call, RelationshipType.USES -----------


def test_resource_referencing_module_resolves_to_module_call(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        module "network" {
          source = "./modules/network"
        }
        resource "aws_instance" "web" {
          subnet_id = module.network.subnet_id
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1

    module_call = next(c for c in components if c.type == "terraform_module_call")
    resource = next(c for c in components if c.name == "aws_instance.web")

    assert relationships[0].source == resource.id
    assert relationships[0].target == module_call.id
    assert relationships[0].relationship_type == "uses"


def test_resource_not_referencing_any_module_produces_no_module_relationship(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        module "network" {
          source = "./modules/network"
        }
        resource "aws_instance" "web" {
          ami = "ami-123"
        }
        """,
    )
    relationships = resolve_references(components)
    assert relationships == []


# --- 15/16. Directory scoping (reusing 6A.7's mechanism) --------------------


def test_module_reference_resolves_within_same_directory(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "environments/staging/main.tf",
        """
        module "network" {
          source = "./modules/network"
        }
        resource "aws_instance" "web" {
          subnet_id = module.network.subnet_id
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"


def test_module_reference_does_not_resolve_across_different_directories(tmp_repo: Path) -> None:
    """The exact false-positive scenario this must guard against: a
    module named "network" in staging must not satisfy a reference to
    module.network from a completely different directory."""
    staging_module = _parse(
        tmp_repo, "environments/staging/network.tf", 'module "network" {\n  source = "./modules/network"\n}\n'
    )
    production_resource = _parse(
        tmp_repo,
        "environments/production/main.tf",
        """
        resource "aws_instance" "web" {
          subnet_id = module.network.subnet_id
        }
        """,
    )
    relationships = resolve_references(staging_module + production_resource)
    assert relationships == []


# --- 17. Module-to-module reference (6B.2b) ----------------------------------


def test_module_to_module_reference_resolves(tmp_repo: Path) -> None:
    """A module call's own body can reference another module call — e.g.
    module "app" passing module "network"'s output as one of its own
    arguments. The same reference-chain machinery already used for
    resource bodies applies equally to module-block bodies."""
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        module "network" {
          source = "./modules/network"
        }
        module "app" {
          source = "./modules/app"
          vpc_id = module.network.vpc_id
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 1

    network_call = next(c for c in components if c.metadata.get("module_name") == "network")
    app_call = next(c for c in components if c.metadata.get("module_name") == "app")

    assert relationships[0].source == app_call.id
    assert relationships[0].target == network_call.id
    assert relationships[0].relationship_type == "uses"


def test_module_to_module_reference_respects_directory_scoping(tmp_repo: Path) -> None:
    staging = _parse(
        tmp_repo,
        "environments/staging/main.tf",
        """
        module "network" {
          source = "./modules/network"
        }
        """,
    )
    production = _parse(
        tmp_repo,
        "environments/production/main.tf",
        """
        module "app" {
          source = "./modules/app"
          vpc_id = module.network.vpc_id
        }
        """,
    )
    relationships = resolve_references(staging + production)
    assert relationships == []


# --- 18. No relationship from mere directory/source similarity -------------


def test_module_calls_with_similar_sources_in_different_directories_do_not_relate(tmp_repo: Path) -> None:
    """Two module calls in different directories that happen to have the
    identical `source` string (e.g. both use "./modules/network") must
    NOT be related to each other — there is no actual reference between
    them, only a coincidental source-string match, which must never be
    treated as evidence of a relationship."""
    staging = _parse(
        tmp_repo, "environments/staging/main.tf", 'module "network" {\n  source = "./modules/network"\n}\n'
    )
    production = _parse(
        tmp_repo, "environments/production/main.tf", 'module "network" {\n  source = "./modules/network"\n}\n'
    )
    relationships = resolve_references(staging + production)
    assert relationships == []


# --- 19/20. Existing behavior unaffected -------------------------------------


def test_existing_resource_to_resource_relationship_unaffected_by_module_support(tmp_repo: Path) -> None:
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


def test_resource_referencing_both_a_resource_and_a_module_produces_both_relationships(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "main.tf",
        """
        module "network" {
          source = "./modules/network"
        }
        resource "aws_vpc" "main" {
          cidr_block = "10.0.0.0/16"
        }
        resource "aws_instance" "web" {
          vpc_id = aws_vpc.main.id
          subnet_id = module.network.subnet_id
        }
        """,
    )
    relationships = resolve_references(components)
    assert len(relationships) == 2
    assert {r.relationship_type for r in relationships} == {"depends_on", "uses"}


def test_directory_scoped_resource_resolution_still_works_alongside_modules(tmp_repo: Path) -> None:
    """Regression for 6A.7: directory-scoped resource-to-resource
    resolution must be completely unaffected by module support existing
    in the same file/parser."""
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
        resource "aws_subnet" "public" {
          vpc_id = aws_vpc.main.id
        }
        """,
    )
    relationships = resolve_references(staging + production)
    assert len(relationships) == 1
    source = next(c for c in staging + production if c.id == relationships[0].source)
    assert source.metadata["source_file"] == "environments/staging/main.tf"


# --- 21. Module components survive graph construction -----------------------


def test_module_call_component_becomes_a_graph_node() -> None:
    """No production change should be needed here — Component.type being
    a plain string and GraphBuilder/refine_node_type having no
    Kubernetes-specific branching for other technologies means a new
    Terraform component type should flow through automatically."""
    components = [
        Component(
            id="terraform:main.tf:module.network",
            name="module.network",
            type="terraform_module_call",
            technology="terraform",
            metadata={"source_file": "main.tf", "module_name": "network", "module_source": "./modules/network"},
        ),
        Component(
            id="terraform:main.tf:aws_instance.web",
            name="aws_instance.web",
            type="terraform_resource",
            technology="terraform",
            metadata={"source_file": "main.tf", "resource_type": "aws_instance", "resource_name": "web"},
        ),
    ]
    relationships = [Relationship(source="terraform:main.tf:aws_instance.web", target="terraform:main.tf:module.network", relationship_type="uses")]
    model = InfrastructureModel(components=components, relationships=relationships)
    engine = GraphEngine.from_infrastructure_model(model, infer=True)

    node = engine.get_node("terraform:main.tf:module.network")
    assert node is not None
    assert node.node_type == "terraform_module_call"  # unrefined — no Kubernetes "kind" to refine on
    assert node.technology == "terraform"

    dependents = engine.get_dependents("terraform:main.tf:module.network")
    assert {n.id for n in dependents} == {"terraform:main.tf:aws_instance.web"}


# --- 22. Module components appear in component discovery/search -----------


def test_module_call_component_appears_in_component_listing(monkeypatch) -> None:
    components = [
        Component(
            id="terraform:main.tf:module.network",
            name="module.network",
            type="terraform_module_call",
            technology="terraform",
            metadata={"source_file": "main.tf", "module_name": "network"},
        ),
    ]
    model = InfrastructureModel(components=components)
    graph = GraphEngine.from_infrastructure_model(model, infer=True)
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: graph)

    result = component_lookup_service.list_components("https://github.com/example/repo")
    assert {s.id for s in result.items} == {"terraform:main.tf:module.network"}
    assert result.items[0].node_type == "terraform_module_call"


def test_module_call_component_filterable_by_node_type(monkeypatch) -> None:
    components = [
        Component(
            id="terraform:main.tf:module.network",
            name="module.network",
            type="terraform_module_call",
            technology="terraform",
            metadata={"source_file": "main.tf", "module_name": "network"},
        ),
        Component(
            id="terraform:main.tf:aws_vpc.main",
            name="aws_vpc.main",
            type="terraform_resource",
            technology="terraform",
            metadata={"source_file": "main.tf", "resource_type": "aws_vpc", "resource_name": "main"},
        ),
    ]
    model = InfrastructureModel(components=components)
    graph = GraphEngine.from_infrastructure_model(model, infer=True)
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: graph)

    result = component_lookup_service.list_components(
        "https://github.com/example/repo", node_type="terraform_module_call"
    )
    assert {s.id for s in result.items} == {"terraform:main.tf:module.network"}
