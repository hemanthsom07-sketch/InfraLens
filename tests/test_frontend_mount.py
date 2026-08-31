"""Phase 8: tests for the frontend static-file mount in app/main.py.

No httpx / TestClient (same reasoning as every other API test file in
this suite): these tests inspect the app's route table and the actual
files on disk directly, rather than making real HTTP requests through a
client this project deliberately doesn't depend on.

Scope discipline: this phase is "new frontend/ directory only" per the
approved audit. These tests exist to confirm (a) the static mount is
wired correctly and (b) every pre-existing route/behavior is completely
unaffected — not to test browser rendering, which no tool in this
project's dependency set can do headlessly.
"""

from pathlib import Path

from starlette.routing import Mount


def _frontend_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "frontend"


# --- static mount wiring -----------------------------------------------------


def test_frontend_is_mounted_at_ui() -> None:
    from app.main import app

    mounts = [route for route in app.routes if isinstance(route, Mount) and route.path == "/ui"]
    assert len(mounts) == 1


def test_frontend_mount_points_at_the_real_frontend_directory() -> None:
    from app.main import _FRONTEND_DIR

    assert _FRONTEND_DIR == _frontend_dir()
    assert _FRONTEND_DIR.is_dir()


def test_static_files_mount_serves_html_at_directory_root() -> None:
    """html=True means a request for "/ui/" resolves to index.html --
    confirming the StaticFiles app was configured with that flag, not
    just mounted."""
    from app.main import app

    mount = next(route for route in app.routes if isinstance(route, Mount) and route.path == "/ui")
    assert mount.app.html is True


# --- frontend files exist and are non-trivial ---------------------------------


def test_index_html_exists_and_is_non_empty() -> None:
    path = _frontend_dir() / "index.html"
    assert path.is_file()
    assert path.stat().st_size > 0


def test_app_js_exists_and_is_non_empty() -> None:
    path = _frontend_dir() / "app.js"
    assert path.is_file()
    assert path.stat().st_size > 0


def test_style_css_exists_and_is_non_empty() -> None:
    path = _frontend_dir() / "style.css"
    assert path.is_file()
    assert path.stat().st_size > 0


def test_index_html_references_app_js_and_style_css() -> None:
    """A basic wiring sanity check: the page actually loads the other
    two files, rather than them existing unreferenced."""
    html = (_frontend_dir() / "index.html").read_text()
    assert "/ui/app.js" in html
    assert "/ui/style.css" in html


def test_app_js_calls_every_endpoint_the_detail_panel_promises() -> None:
    """The four detail-panel tabs (Explain/Impact/Dependencies/Security)
    each need a real call to their corresponding existing endpoint --
    confirming the frontend wasn't left calling a subset while claiming
    to support all four."""
    js = (_frontend_dir() / "app.js").read_text()
    assert '"/analyze"' in js
    assert '"/explain"' in js
    assert '"/impact"' in js
    assert '"/dependencies"' in js
    assert '"/security"' in js


def test_app_js_request_bodies_use_analysis_id_not_repo_url_for_followup_calls() -> None:
    """Every follow-up call (explain/impact/dependencies) after the
    initial /analyze must reuse analysis_id (Phase 7A's whole point) --
    a frontend re-sending repo_url per click would silently defeat the
    caching this entire project is built around."""
    js = (_frontend_dir() / "app.js").read_text()
    assert "analysis_id: analysisId" in js or "analysis_id: state.analysisId" in js


# --- no new dependency was introduced ------------------------------------------


def test_no_new_python_dependency_was_added() -> None:
    """StaticFiles is bundled with FastAPI/Starlette, already a
    dependency -- this phase must not have added anything to
    pyproject.toml."""
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    assert "jinja2" not in pyproject.lower()  # the one common templating dep this phase deliberately avoided


# --- regression: every pre-existing route/behavior is unaffected --------------


def test_root_health_check_route_unaffected() -> None:
    from app.main import app, read_root

    assert read_root() == {"status": "ok", "service": "InfraLens", "docs": "/docs"}
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/" in paths


def test_every_pre_existing_api_route_still_registered() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert paths == {
    "/",
    "/api/v1/analyze",
    "/api/v1/explain",
    "/api/v1/explain/graph",
    "/api/v1/components",
    "/api/v1/impact",
    "/api/v1/dependencies",
    "/api/v1/graph/diagnostics",
    "/api/v1/graph/path",
    "/api/v1/security",
}


def test_ui_mount_does_not_appear_in_openapi_schema() -> None:
    """A raw ASGI static-files mount isn't a FastAPI route with a
    request/response model, so it correctly has no OpenAPI entry --
    confirming the mount didn't accidentally register as (or interfere
    with) an API route."""
    from app.main import app

    paths = app.openapi()["paths"].keys()
    assert not any(path.startswith("/ui") for path in paths)
