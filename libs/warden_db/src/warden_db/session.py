"""SQLAlchemy async engine and session factories.

These are FACTORY functions, not module-level singletons.  In async web
applications, creating an Engine at import time is an anti-pattern because:
  1. The DSN may not be available until settings are resolved.
  2. Module-level engines make unit testing hard (you cannot swap them).
  3. Lifespan management (connect-on-startup, dispose-on-shutdown) belongs in
     the application framework (FastAPI lifespan), not at import time.

Usage (FastAPI lifespan):
    engine = make_engine(settings.database_url.get_secret_value())
    session_factory = make_sessionmaker(engine)
    app.state.sessionmaker = session_factory
    yield
    await engine.dispose()
"""

import urllib.parse
import uuid

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


def _normalize_db_url(url: str) -> str:
    """Normalize a database URL, handling unencoded special characters in passwords.

    When environment variable substitution (e.g. Docker Compose) injects passwords
    containing '@' without percent-encoding, the URL contains multiple '@' characters.
    Splitting on the rightmost '@' isolates user:password from host:port/db, allowing
    safe percent-encoding of the password.
    """
    prefix, sep, rest = url.partition("://")
    if not sep:
        return url
    if rest.count("@") > 1:
        auth, _, host_db = rest.rpartition("@")
        user, _, password = auth.partition(":")
        encoded_password = urllib.parse.quote_plus(password)
        return f"{prefix}://{user}:{encoded_password}@{host_db}"
    return url


def make_engine(url: str, *, transaction_pool: bool = False) -> AsyncEngine:
    """Create an async SQLAlchemy engine with production-grade pool settings.

    WHY asyncpg?  It is a native, pure-asyncio Postgres driver that does not
    use thread-pool executors (unlike psycopg2 + greenlet).  This gives true
    async IO without hidden blocking.

    pool_size=10  - max simultaneous DB connections held open.
    max_overflow=5 - extra connections allowed above pool_size under burst load.
    pool_pre_ping=True - issue a cheap SELECT before giving a connection from
      the pool.  Detects stale/closed connections (e.g. Postgres restart) and
      silently replaces them, preventing "connection closed" errors in prod.

    The URL scheme MUST be postgresql+asyncpg:// (not postgresql://) because
    SQLAlchemy uses the scheme to select the correct async dialect.
    """
    url = _normalize_db_url(url)
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError("Database URL must use postgresql+asyncpg:// scheme")

    if transaction_pool:
        # Requires PgBouncer >=1.21 with max_prepared_statements > 0: SQLAlchemy
        # calls prepare() even with its cache disabled. LISTEN and migrations
        # deliberately use the default direct/session engine instead.
        return create_async_engine(
            url,
            poolclass=NullPool,
            pool_pre_ping=True,
            connect_args={
                "prepared_statement_cache_size": 0,
                "statement_cache_size": 0,
                "prepared_statement_name_func": lambda: "__warden_" + uuid.uuid4().hex,
            },
        )

    return create_async_engine(
        url,
        pool_size=10,
        max_overflow=5,
        pool_pre_ping=True,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the given engine.

    WHY expire_on_commit=False?
      In synchronous SQLAlchemy, after session.commit(), all attributes on ORM
      objects are expired so the next access triggers a lazy SELECT refresh.
      In async code, that lazy load would happen outside the request context
      (no active session) and raise MissingGreenlet / DetachedInstanceError.
      Setting expire_on_commit=False keeps attribute values alive after commit
      so callers can safely read them from the returned objects.

    WHY async_sessionmaker (not sessionmaker + class_=AsyncSession)?
      async_sessionmaker is the typed, purpose-built factory introduced in
      SQLAlchemy 2.0.  It returns AsyncSession directly (no cast needed) and
      its type parameters are understood by mypy without extra stubs.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
