"""Parses *.tf files for `resource "type" "name" { ... }` blocks.

HCL has its own grammar, but a resource declaration's header is simple
and highly regular, so extracting just the type and name — all Phase 3
asks for — is reliable with a single regex rather than a full HCL
parser. The pattern doesn't hardcode provider-specific resource types
(aws_instance, google_compute_instance, ...), so any provider's
resources are picked up the same way. A real HCL parser would be worth
adding if a later phase needs resource *attributes* too, not just their
type and name.
"""

import re
from pathlib import Path

from app.models.ikm import Component, ComponentType, InfrastructureModel
from app.parsers.base import InfrastructureParser

_RESOURCE_BLOCK_RE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{')
_LINE_COMMENT_RE = re.compile(r"^\s*(#|//)")


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
                    },
                )
            )

        return InfrastructureModel(components=components)