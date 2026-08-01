"""Parses *.tf files for `resource "type" "name" { ... }` blocks, and the
references between them.

HCL has its own grammar, but a resource declaration's header is simple
and highly regular, so extracting the type and name is reliable with a
single regex rather than a full HCL parser.

Resource *references* (explicit `depends_on = [...]`, and implicit
interpolation like `vpc_id = aws_vpc.main.id`) both use the exact same
`resource_type.resource_name` syntax in Terraform — so rather than
hardcoding a list of specific patterns (VPC->Subnet, SG->EC2, IAM->Lambda,
...), this parser detects that one general syntactic pattern and
cross-checks each candidate against resources actually declared. That
naturally produces every one of those specific patterns (and any other
provider's, not just AWS) as instances of the same mechanism, without a
hardcoded table to maintain — and it's exactly as correct as Terraform's
own dependency graph, since it's driven by the same reference syntax.

Cross-file correlation (a resource in ec2.tf referencing one declared in
vpc.tf — a completely normal way to structure a real Terraform project)
can't happen inside parse() itself, since each call only ever sees one
file. So this module also exposes resolve_references(), a module-level
function ikm_service.build_infrastructure_model() calls once every file
has been parsed, the same way kubernetes_parser.resolve_references()
works for the analogous Kubernetes case.
"""

import re
from pathlib import Path

from app.models.ikm import Component, ComponentType, InfrastructureModel, Relationship, RelationshipType
from app.parsers.base import InfrastructureParser

_RESOURCE_BLOCK_RE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{')
_LINE_COMMENT_RE = re.compile(r"^\s*(#|//)")

# Matches a *whole* dotted identifier chain (2 or more segments) as one
# unit — e.g. "aws_vpc.main", "aws_vpc.main.id", or "data.aws_subnet.x.id".
# Only the first two segments are ever used (see _candidate_reference
# below); matching the whole chain in one piece, rather than scanning for
# any 2-segment substring, is what stops a chain like "data.aws_subnet.x.id"
# from being read as two separate references after "data" is excluded —
# the previous version of this regex had exactly that bug.
_REFERENCE_CHAIN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)+\b")

# type.name-shaped prefixes that are never a resource reference —
# variables, locals, module outputs, and data sources all use the same
# dotted syntax for a different purpose, so they'd otherwise look
# identical to one.
_NON_RESOURCE_PREFIXES = frozenset(
    {"var", "local", "module", "data", "output", "terraform", "path", "each", "count", "self"}
)


def _candidate_reference(chain: str) -> tuple[str, str] | None:
    """From a full dotted chain like "aws_vpc.main.id", return just the
    (type, name) leading pair — "aws_vpc.main.id" and "aws_vpc.main" both
    yield ("aws_vpc", "main"); anything with a non-resource prefix (e.g.
    "data.aws_subnet.x.id") returns None."""
    segments = chain.split(".")
    ref_type, ref_name = segments[0], segments[1]
    if ref_type in _NON_RESOURCE_PREFIXES:
        return None
    return ref_type, ref_name


def _extract_block_body(text: str, open_brace_index: int) -> str:
    """Given the index of a block's opening '{', return everything up to
    (not including) its matching closing '}', using balanced brace
    counting. A plain regex can't correctly handle arbitrary nesting
    (e.g. tags = { Name = "x" } inside a resource block)."""
    depth = 0
    i = open_brace_index
    body_start = open_brace_index + 1
    length = len(text)
    while i < length:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[body_start:i]
        i += 1
    return text[body_start:]  # unterminated block (malformed file) — best effort


class TerraformParser(InfrastructureParser):
    """Parses a single .tf file into one component per resource block."""

    def parse(self, path: Path, repo_root: Path) -> InfrastructureModel:
        text = self._read_text(path)
        if text is None:
            return InfrastructureModel()

        # Strip fully-commented-out lines so a commented resource block
        # isn't mistaken for a real one. Block comments (/* ... */) and
        # inline trailing comments are left alone — a resource header
        # (which is what the regex below matches) is never legitimately
        # split across those anyway.
        active_lines = (line for line in text.splitlines() if not _LINE_COMMENT_RE.match(line))
        text = "\n".join(active_lines)

        relative_id = self._relative_id(path, repo_root)
        components: list[Component] = []

        for match in _RESOURCE_BLOCK_RE.finditer(text):
            resource_type, resource_name = match.group(1), match.group(2)
            # match ends right after the opening '{', so that's at index end()-1.
            body = _extract_block_body(text, match.end() - 1)

            referenced_identifiers: set[str] = set()
            for chain in _REFERENCE_CHAIN_RE.findall(body):
                candidate = _candidate_reference(chain)
                if candidate is None:
                    continue
                ref_type, ref_name = candidate
                if (ref_type, ref_name) != (resource_type, resource_name):
                    referenced_identifiers.add(f"{ref_type}.{ref_name}")

            components.append(
                Component(
                    id=f"terraform:{relative_id}:{resource_type}.{resource_name}",
                    name=f"{resource_type}.{resource_name}",
                    type=ComponentType.TERRAFORM_RESOURCE,
                    technology="terraform",
                    metadata={
                        "source_file": relative_id,
                        "resource_type": resource_type,
                        "resource_name": resource_name,
                        # Raw candidates only — not yet cross-checked against
                        # resources declared in *other* files. That happens
                        # in resolve_references(), below, once every .tf
                        # file has been parsed.
                        "referenced_identifiers": sorted(referenced_identifiers),
                    },
                )
            )

        return InfrastructureModel(components=components)


def resolve_references(components: list[Component]) -> list[Relationship]:
    """Cross-check every Terraform component's referenced_identifiers
    against every Terraform component actually declared (possibly in a
    different .tf file), producing a depends_on relationship for each
    real match. Called once from ikm_service.build_infrastructure_model(),
    after every file has already been parsed.
    """
    terraform_components = [c for c in components if c.technology == "terraform"]
    by_identifier = {f"{c.metadata['resource_type']}.{c.metadata['resource_name']}": c for c in terraform_components}

    relationships: list[Relationship] = []
    for component in terraform_components:
        for identifier in component.metadata.get("referenced_identifiers", []):
            target = by_identifier.get(identifier)
            if target is not None:
                relationships.append(
                    Relationship(source=component.id, target=target.id, relationship_type=RelationshipType.DEPENDS_ON)
                )
    return relationships
