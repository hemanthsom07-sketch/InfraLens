# InfraLens

> AI-powered Infrastructure Analysis Platform

InfraLens is a portfolio project that analyzes infrastructure inside GitHub repositories. Given a public GitHub repository URL, it clones the repository, scans its contents, detects technologies, and progressively builds an intelligent understanding of the project's infrastructure.

This project is being developed phase by phase, with each phase delivering a working feature.

---

# Current Status

**Current Phase:** Phase 1 – Repository Analysis ✅

Implemented features:

- Clone a public GitHub repository
- Validate GitHub repository URLs
- Scan repository contents
- Count total files
- Detect programming languages using file extensions
- Generate a complete directory tree
- Automatic cleanup of temporary cloned repositories
- Interactive Swagger API documentation

---

# Tech Stack

## Backend

- Python 3.12
- FastAPI
- Pydantic v2
- Uvicorn
- uv (Package Manager)

## Tools

- Git CLI

---

# Project Structure

```text
InfraLens/
│
├── app/
│   ├── api/
│   │   └── v1/
│   │       └── analyze.py
│   │
│   ├── core/
│   │   └── config.py
│   │
│   ├── models/
│   │   └── schemas.py
│   │
│   ├── services/
│   │   ├── git_service.py
│   │   └── scanner_service.py
│   │
│   ├── exceptions.py
│   └── main.py
│
├── pyproject.toml
├── README.md
├── .gitignore
└── .python-version
```

---

# Installation

Clone the repository:

```bash
git clone https://github.com/hemanthsom07-sketch/InfraLens.git
```

Move into the project:

```bash
cd InfraLens
```

Install dependencies:

```bash
uv sync
```

---

# Run the Project

```bash
uv run uvicorn app.main:app --reload
```

Server:

```
http://127.0.0.1:8000
```

Swagger Documentation:

```
http://127.0.0.1:8000/docs
```

---

# API

## Analyze Repository

**POST**

```
/api/v1/analyze
```

Request

```json
{
  "repo_url": "https://github.com/octocat/Hello-World"
}
```

Example Response

```json
{
  "repository": "Hello-World",
  "total_files": 12,
  "languages": [
    "Python",
    "Markdown"
  ],
  "tree": [
    ...
  ]
}
```

---

# Roadmap

## ✅ Phase 1
- Repository cloning
- Repository scanning
- File tree generation
- Language detection

## 🚧 Phase 2
- Detect infrastructure technologies
  - Docker
  - Docker Compose
  - Terraform
  - Kubernetes
  - Helm

## 📌 Phase 3
- Infrastructure parser

## 📌 Phase 4
- Dependency graph generation

## 📌 Phase 5
- AI-powered infrastructure explanation

## 📌 Phase 6
- Security analysis

## 📌 Phase 7
- Cost estimation

## 📌 Phase 8
- Interactive frontend

## 📌 Phase 9
- Deployment

---

# Current Limitations

- Only public GitHub repositories are supported.
- Language detection is based only on file extensions.
- No authentication.
- No database.
- No AI analysis yet.
- No frontend yet.

---

# Future Vision

InfraLens aims to become an intelligent infrastructure analysis platform capable of:

- Repository analysis
- Infrastructure visualization
- Dependency graph generation
- AI-powered explanations
- Security insights
- Cloud cost estimation
- Architecture recommendations

---

# License

This project is currently developed for educational and portfolio purposes.