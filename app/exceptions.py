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


class AnalysisNotFoundError(InfraLensError):
    """No analysis exists for the given analysis_id (Phase 7A).

    Covers both cases identically, since neither is distinguishable
    from the other from the outside: the id was never created, or it
    was created but has since expired/been evicted from the in-memory
    AnalysisStore (see app.services.analysis_store).
    """

    def __init__(self, analysis_id: str) -> None:
        self.analysis_id = analysis_id
        super().__init__(f"No analysis found for analysis_id '{analysis_id}'.")
