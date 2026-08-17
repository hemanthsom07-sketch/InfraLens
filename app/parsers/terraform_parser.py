"""Parses *.tf files for `resource "type" "name" { ... }` blocks and
`module "name" { ... }` blocks, and the references between them.

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

MODULE CALLS (Phase 6B): a `module "name" { source = ... }` block is
parsed the same way a resource block is — same balanced-brace body
extraction, same dotted-reference-chain scanning — into a
"terraform_module_call" component. This is deliberately coarse: it
records that a module call exists and what references it, never what's
*inside* the module (recursive child-module parsing, resolving a
specific `module.x.output_name` into the internal resource that produces
it, and registry/git module downloading are all explicitly out of scope
here — see the module's own docstring notes below for what "coarse"
means precisely). A `module.x` reference is captured separately from
ordinary resource references (`referenced_module_calls`, not
`referenced_identifiers`) so existing resource-to-resource resolution
behavior is completely unaffected by this addition.
"""

import posixpath
import re
from pathlib import Path, PurePosixPath
from typing import Any

from app.models.ikm import Component, ComponentType, InfrastructureModel, Relationship, RelationshipType
from app.parsers.base import InfrastructureParser

_RESOURCE_BLOCK_RE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{')
_LINE_COMMENT_RE = re.compile(r"^\s*(#|//)")

# Phase 6B: `module "name" { ... }` block header, and the `source = "..."`
# argument inside its body.
_MODULE_BLOCK_RE = re.compile(r'module\s+"([^"]+)"\s*\{')
_MODULE_SOURCE_RE = re.compile(r'source\s*=\s*"([^"]*)"')

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
# identical to one. "module" specifically is handled by
# _candidate_module_reference below, not treated as a resource reference,
# but it stays excluded here too so referenced_identifiers (the
# resource-to-resource collection) never picks up a module reference by
# accident.
_NON_RESOURCE_PREFIXES = frozenset(
    {"var", "local", "module", "data", "output", "terraform", "path", "each", "count", "self"}
)


def _candidate_reference(chain: str) -> tuple[str, str] | None:
    """From a full dotted chain like "aws_vpc.main.id", return just the
    (type, name) leading pair — "aws_vpc.main.id" and "aws_vpc.main" both
    yield ("aws_vpc", "main"); anything with a non-resource prefix (e.g.
    "data.aws_subnet.x.id") returns None.

    Deliberately returns None for a "module.x..." chain too — that's
    _candidate_module_reference's job (below), kept as a fully separate
    collection (referenced_module_calls) so this function's existing
    resource-reference behavior is completely unaffected by Phase 6B.
    """
    segments = chain.split(".")
    ref_type, ref_name = segments[0], segments[1]
    if ref_type in _NON_RESOURCE_PREFIXES:
        return None
    return ref_type, ref_name


def _candidate_module_reference(chain: str) -> str | None:
    """From a full dotted chain like "module.network.vpc_id", return just
    the module call name ("network") — or None if this chain isn't a
    module-call reference. Used to build referenced_module_calls, kept
    fully separate from _candidate_reference/referenced_identifiers."""
    segments = chain.split(".")
    if segments[0] != "module" or len(segments) < 2:
        return None
    return segments[1]


def _resolve_local_module_directory(calling_source_file: str, local_source: str) -> str | None:
    """Resolve a local relative module `source` (e.g. "./modules/network")
    against the directory containing `calling_source_file`, returning a
    normalized, repo-root-relative POSIX path to where the module's own
    .tf files would be — the same relative-path resolution Terraform
    itself performs (a module source is always relative to the calling
    file's directory).

    Purely a string computation: this does NOT mean the target directory
    has been parsed, has any .tf files, or even exists in this
    repository — it only identifies where the local source appears to
    point. Returns None if the normalized result would fall outside the
    repository root (leading ".." after normalization) — in that case
    there's no honest repo-relative path to report, so nothing is
    guessed.
    """
    calling_directory = PurePosixPath(calling_source_file).parent
    joined = (calling_directory / local_source).as_posix()
    normalized = posixpath.normpath(joined)
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


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
            referenced_module_calls: set[str] = set()
            for chain in _REFERENCE_CHAIN_RE.findall(body):
                module_call_name = _candidate_module_reference(chain)
                if module_call_name is not None:
                    referenced_module_calls.add(module_call_name)
                    continue
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
                        # resources/module calls declared in *other* files.
                        # That happens in resolve_references(), below, once
                        # every .tf file has been parsed.
                        "referenced_identifiers": sorted(referenced_identifiers),
                        "referenced_module_calls": sorted(referenced_module_calls),
                    },
                )
            )

        for match in _MODULE_BLOCK_RE.finditer(text):
            call_name = match.group(1)
            body = _extract_block_body(text, match.end() - 1)

            source_match = _MODULE_SOURCE_RE.search(body)
            source = source_match.group(1) if source_match and source_match.group(1) else None

            referenced_module_calls = set()
            for chain in _REFERENCE_CHAIN_RE.findall(body):
                referenced_call_name = _candidate_module_reference(chain)
                if referenced_call_name is not None and referenced_call_name != call_name:
                    referenced_module_calls.add(referenced_call_name)

            module_metadata: dict[str, Any] = {
                "source_file": relative_id,
                "module_name": call_name,
                "referenced_module_calls": sorted(referenced_module_calls),
            }
            # No source at all: still a real module call worth recording
            # (do not guess, do not crash — just omit the source-related
            # keys, following the same "omit an optional key rather than
            # fabricate a value" convention used elsewhere in this project,
            # e.g. Kubernetes namespace capture).
            if source:
                module_metadata["module_source"] = source
                is_local_source = source.startswith("./") or source.startswith("../")
                module_metadata["is_local_source"] = is_local_source
                if is_local_source:
                    resolved_directory = _resolve_local_module_directory(relative_id, source)
                    if resolved_directory is not None:
                        module_metadata["resolved_source_directory"] = resolved_directory

            components.append(
                Component(
                    id=f"terraform:{relative_id}:module.{call_name}",
                    name=f"module.{call_name}",
                    type="terraform_module_call",
                    technology="terraform",
                    metadata=module_metadata,
                )
            )

        return InfrastructureModel(components=components)


def resolve_references(components: list[Component]) -> list[Relationship]:
    """Cross-check every Terraform component's referenced_identifiers
    (resource-to-resource) and referenced_module_calls (resource-to-module
    and module-to-module — Phase 6B) against Terraform components actually
    declared — possibly in a different .tf file — producing a relationship
    for each real match. Called once from
    ikm_service.build_infrastructure_model(), after every file has already
    been parsed.

    SCOPE (Phase 6A.7, reused unchanged for module calls in Phase 6B):
    resolution is scoped to same-DIRECTORY .tf files only — never the
    whole repo. A flat, repo-wide lookup would let same-named resources
    (or module calls) in different environment directories (a
    near-universal `environments/staging/`, `environments/production/`
    layout) collide. This is the same reasoning from 6A.7, applied to the
    same directory-scoped lookup structure — not a second, incompatible
    scope mechanism. For module calls specifically, directory scoping
    isn't just conservative: a reference to `module.x` is only ever valid
    within the same configuration that declares `module "x" {}`, so
    same-directory-only is the semantically correct scope, not merely a
    risk-reduction heuristic.

    Module calls are recorded (component.type == "terraform_module_call")
    but never treated as a resource: they don't have resource_type/
    resource_name metadata, so they're excluded from the resource lookup
    map below to avoid a KeyError, and their own referenced_identifiers
    is simply absent (never populated for a module-call component) rather
    than checked.
    """
    terraform_components = [c for c in components if c.technology == "terraform"]
    resource_components = [c for c in terraform_components if c.type == ComponentType.TERRAFORM_RESOURCE]
    module_call_components = [c for c in terraform_components if c.type == "terraform_module_call"]

    by_directory_and_identifier: dict[tuple[str, str], Component] = {
        (
            str(PurePosixPath(c.metadata["source_file"]).parent),
            f"{c.metadata['resource_type']}.{c.metadata['resource_name']}",
        ): c
        for c in resource_components
    }
    by_directory_and_module_name: dict[tuple[str, str], Component] = {
        (str(PurePosixPath(c.metadata["source_file"]).parent), c.metadata["module_name"]): c
        for c in module_call_components
    }

    relationships: list[Relationship] = []
    for component in terraform_components:
        directory = str(PurePosixPath(component.metadata["source_file"]).parent)

        for identifier in component.metadata.get("referenced_identifiers", []):
            target = by_directory_and_identifier.get((directory, identifier))
            if target is not None:
                relationships.append(
                    Relationship(source=component.id, target=target.id, relationship_type=RelationshipType.DEPENDS_ON)
                )

        for module_call_name in component.metadata.get("referenced_module_calls", []):
            target = by_directory_and_module_name.get((directory, module_call_name))
            if target is not None:
                relationships.append(
                    Relationship(source=component.id, target=target.id, relationship_type=RelationshipType.USES)
                )

    return relationships
