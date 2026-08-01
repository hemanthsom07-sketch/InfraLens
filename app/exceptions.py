"""Custom exceptions for InfraLens.

Each is mapped to an HTTP response in app/main.py, so route handlers and
services can just raise these and stay focused on business logic instead
of HTTP concerns.
"""


class InfraLensError(Exception):
    """Base class for all InfraLens application-specific errors."""


class InvalidRepositoryURLError(InfraLensError):
    """The given string is not a valid GitHub repository URL."""


class RepositoryCloneError(InfraLensError):
    """The repository could not be cloned.

    Covers: repo not found, private repo, network failure, clone timeout,
    or git being unavailable on the server — all of these are, from the
    API's point of view, "we couldn't get the repository's contents."
    """
