"""InfraLens FastAPI application entrypoint.

Run with:  uv run uvicorn app.main:app --reload
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.analyze import router as analyze_router
from app.exceptions import InvalidRepositoryURLError, RepositoryCloneError

app = FastAPI(
    title="InfraLens",
    description=(
        "AI-powered Infrastructure Analysis Platform — repository scanning, "
        "language/framework/infrastructure detection, and structured "
        "infrastructure understanding via the Infrastructure Knowledge Model."
    ),
    version="0.3.0",
)


# --- Global exception handlers -------------------------------------------
# Registering these here (rather than try/except in every route) means any
# future route can raise these same exceptions and get consistent,
# well-formed JSON error responses for free.


@app.exception_handler(InvalidRepositoryURLError)
async def invalid_url_handler(request: Request, exc: InvalidRepositoryURLError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(RepositoryCloneError)
async def clone_error_handler(request: Request, exc: RepositoryCloneError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


# --- Routes ----------------------------------------------------------------

app.include_router(analyze_router, prefix="/api/v1", tags=["Analysis"])


@app.get("/", tags=["Health"])
def read_root() -> dict[str, str]:
    """Simple liveness check / landing endpoint."""
    return {"status": "ok", "service": "InfraLens", "docs": "/docs"}