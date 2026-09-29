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

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str) -> AsyncEngine:
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
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError(f"Database URL must use postgresql+asyncpg:// scheme, got: {url[:30]}...")

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
