"""Infrastructure Knowledge Model (IKM).

A technology-agnostic representation that every infrastructure parser
(Docker, Compose, Terraform, Kubernetes, and anything added later)
converts its findings into. Downstream consumers — a future graph
engine, AI explanation engine, security analysis, cost analysis,
frontend visualization — work against this one model instead of needing
to understand every source technology individually.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ComponentType(StrEnum):
    """Common component types.

    Not exhaustive on purpose: Component.type below is a plain str so a
    parser can introduce a new type (e.g. "ingress") without a model
    change. These are just the well-known ones, kept here so parsers
    build them consistently instead of hand-typing strings.
    """

    CONTAINER = "container"
    SERVICE = "service"
    DATABASE = "database"
    NETWORK = "network"
    VOLUME = "volume"
    TERRAFORM_RESOURCE = "terraform_resource"
    KUBERNETES_RESOURCE = "kubernetes_resource"


class RelationshipType(StrEnum):
    """Common relationship types — see the ComponentType docstring; the
    same open/extensible reasoning applies here."""

    CONNECTS_TO = "connects_to"
    DEPENDS_ON = "depends_on"
    USES = "uses"
    CONTAINS = "contains"
    MOUNTS = "mounts"


class Component(BaseModel):
    """A single infrastructure element — a container, a database, a
    Terraform resource, a Kubernetes object, etc."""

    id: str = Field(..., description="Unique identifier within the InfrastructureModel.")
    name: str = Field(..., description="Human-readable name.")
    type: str = Field(
        ...,
        description="Component category, e.g. 'container', 'database'. See ComponentType for common values.",
    )
    technology: str = Field(..., description="Source technology, e.g. 'docker', 'terraform', 'kubernetes'.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Technology-specific details (ports, image, env vars, ...).",
    )


class Relationship(BaseModel):
    """A directed edge between two components, referenced by Component.id."""

    source: str = Field(..., description="Source Component.id.")
    target: str = Field(..., description="Target Component.id.")
    relationship_type: str = Field(
        ...,
        description="Edge label, e.g. 'depends_on', 'mounts'. See RelationshipType for common values.",
    )


class InfrastructureModel(BaseModel):
    """The unified representation every parser contributes components
    and relationships to."""

    components: list[Component] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)