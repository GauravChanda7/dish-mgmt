"""Engine and session setup.

The only thing that changes between SQLite and a production database is the
DATABASE_URL environment variable, for example:

    sqlite:///./dish_mgmt.db                      (default)
    postgresql+psycopg://user:pw@host:5432/dish
    mysql+pymysql://user:pw@host:3306/dish
"""

import os
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./dish_mgmt.db"


def _normalize_url(url: str) -> str:
    """Hosts such as Render hand out postgres:// or postgresql://, which
    SQLAlchemy would send to the psycopg2 driver. We install psycopg 3."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url: str | None = None, **kwargs) -> Engine:
    url = _normalize_url(url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        # SQLite ignores foreign keys unless asked. MySQL and PostgreSQL enforce them.
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=True, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
