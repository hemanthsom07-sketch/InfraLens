"""GitHub URL validation and repository cloning via the git CLI."""

import re
import subprocess
from pathlib import Path

from app.exceptions import InvalidRepositoryURLError, RepositoryCloneError

# Accepts https://github.com/<owner>/<repo>, tolerating an optional trailing
# ".git", a trailing slash, or extra path segments (e.g. "/tree/main"). Extra
# segments are ignored — only owner/repo are ever used to build the actual
# clone URL, so a link copied from a subfolder still resolves to its repo.
_GITHUB_URL_PATTERN = re.compile(
    r"^https?://github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
    r"(?P<repo>[A-Za-z0-9_.-]+?)"
    r"(?:\.git)?"
    r"(?:/.*)?/?$",
    re.IGNORECASE,
)

CLONE_TIMEOUT_SECONDS = 60


def parse_github_url(repo_url: str) -> tuple[str, str]:
    """Validate `repo_url` and extract (owner, repo).

    Raises InvalidRepositoryURLError if it isn't a well-formed
    https://github.com/<owner>/<repo> URL.
    """
    if not repo_url or not repo_url.strip():
        raise InvalidRepositoryURLError("Repository URL cannot be empty.")

    match = _GITHUB_URL_PATTERN.match(repo_url.strip())
    if not match:
        raise InvalidRepositoryURLError(
            "Invalid GitHub repository URL. Expected format: "
            "https://github.com/<owner>/<repository>"
        )
    return match.group("owner"), match.group("repo")


def clone_repository(owner: str, repo: str, destination: Path) -> None:
    """Shallow-clone a public GitHub repository into `destination`.

    `destination` must already exist and be empty — a fresh
    tempfile.TemporaryDirectory() works well here. We always reconstruct a
    clean clone URL from the validated (owner, repo) pair rather than
    reusing the user's raw input, since the raw URL may contain extra path
    segments that `git clone` would not understand.
    """
    clone_url = f"https://github.com/{owner}/{repo}.git"

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", clone_url, str(destination)],
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepositoryCloneError(
            f"Cloning '{owner}/{repo}' took too long and was aborted."
        ) from exc
    except FileNotFoundError as exc:
        # The git executable itself isn't installed / on PATH.
        raise RepositoryCloneError(
            "Git is not installed or not available on the server's PATH."
        ) from exc

    if result.returncode != 0:
        raise RepositoryCloneError(
            f"Could not clone '{owner}/{repo}'. It may not exist, be private, "
            "or the URL may be incorrect."
        )