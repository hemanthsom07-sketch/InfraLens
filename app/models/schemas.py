"""Pydantic request/response models for the analysis API."""

from typing import Literal

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    """Request body for POST /api/v1/analyze."""

    repo_url: str = Field(
        ...,
        description="URL of a public GitHub repository.",
        examples=["https://github.com/octocat/Hello-World"],
    )


class TreeNode(BaseModel):
    """A single file or directory entry in the repository's directory tree."""

    name: str
    type: Literal["file", "directory"]
    children: list["TreeNode"] | None = Field(
        default=None,
        description="Nested entries for directories. Always null for files.",
    )


# TreeNode refers to itself in its own field annotation. model_rebuild()
# resolves that forward reference so the recursive model works correctly.
TreeNode.model_rebuild()


class AnalyzeResponse(BaseModel):
    """Response body for POST /api/v1/analyze."""

    repository: str = Field(..., description="The repository name.")
    total_files: int = Field(..., description="Total number of files found in the repository.")
    languages: list[str] = Field(
        ...,
        description="Detected languages, ordered by number of files using them (most common first).",
    )
    tree: list[TreeNode] = Field(..., description="Directory tree of the repository's contents.")