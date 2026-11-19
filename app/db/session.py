"""Database connection for the app's own tables.

- get_engine(): one SQLAlchemy "engine" (a pool of connections to Postgres), built once from
  DATABASE_URL and reused.
- get_db(): a FastAPI dependency. A route that declares `db: DB = Depends(get_db)` receives a
  fresh database session for that request; it is closed automatically when the request ends.
  Changes are saved only when the route calls db.commit().

The LangGraph checkpointer uses its own connections (app/graph/checkpointer.py), not these.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one DB session per request."""
    with _session_factory()() as db:
        yield db
