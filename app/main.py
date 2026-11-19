"""FastAPI entry point: creates the app and its routes (for now only /health)."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.session import get_engine

app = FastAPI(title="Interview Prep Voice Agent")


@app.get("/health")
def health() -> JSONResponse:
    """Liveness plus a DB check: is Postgres up and is pgvector installed?"""
    try:
        with get_engine().connect() as conn:
            pgvector = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar()
    except Exception as exc:  # report, don't crash the health endpoint
        return JSONResponse(status_code=503, content={"status": "error", "db": str(exc)})

    ok = pgvector is not None
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "db": "ok", "pgvector": pgvector},
    )
