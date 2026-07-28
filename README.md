# InfraLens — Phase 1

AI-powered Infrastructure Analysis Platform. Give it a public GitHub
repository URL and it clones the repo, scans its contents, and reports
back the file count, detected languages, and full directory tree.

This is **Phase 1: repository ingestion and scanning**. Parsing
infrastructure files (Dockerfiles, Terraform, Kubernetes manifests) and
AI-generated explanations are planned for later phases.

## Tech stack

- Python 3.11+
- FastAPI
- Pydantic v2
- uv (dependency management)
- `git` CLI (must be installed and on PATH — used to clone repositories)

No database, no auth, no Docker, no AI calls in this phase.

## Project structure

```
infralens/
├── app/
│   ├── main.py                # FastAPI app, exception handlers, router registration
│   ├── api/v1/analyze.py      # POST /api/v1/analyze
│   ├── core/config.py         # Language map, ignored directories
│   ├── models/schemas.py      # Pydantic request/response models
│   ├── services/git_service.py       # URL validation + git clone
│   └── services/scanner_service.py   # Directory tree + language detection
├── pyproject.toml
├── .python-version
└── .gitignore
```

## Setup

```bash
uv sync
```

This creates a `.venv/` and installs dependencies from `pyproject.toml`.

## Run

```bash
uv run uvicorn app.main:app --reload
```

- API root: http://127.0.0.1:8000/
- Swagger UI: http://127.0.0.1:8000/docs
- OpenAPI schema: http://127.0.0.1:8000/openapi.json

## Example request

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/octocat/Hello-World"}'
```

## Notes / current limitations

- Only public repositories are supported (no authentication in Phase 1).
- Clones are shallow (`--depth 1`) and always deleted after analysis, even
  if an error occurs mid-request.
- Language detection is based on file extension, not file content.
- No CORS middleware yet — not needed until a frontend on a different
  origin is introduced in a later phase.
