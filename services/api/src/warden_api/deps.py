"""FastAPI dependency injection helpers for the WARDEN API.

WHAT IS DEPENDENCY INJECTION IN FASTAPI?
=========================================
FastAPI has a built-in "dependency injection" system. Instead of each route
function creating its own database connection or reading its own settings,
you declare WHAT you need and FastAPI provides it.

Example route using get_session:

    @router.get("/monitors")
    async def list_monitors(session: AsyncSession = Depends(get_session)):
        result = await session.execute(select(Monitor))
        return result.scalars().all()

FastAPI calls get_session() automatically before calling list_monitors().
After list_monitors() returns, FastAPI runs the code AFTER `yield` in
get_session() to clean up.

WHY USE app.state INSTEAD OF A MODULE-LEVEL ENGINE?
====================================================
app.state is FastAPI's built-in storage for objects that should live for the
entire application lifetime. The engine (database connection pool) is created
once during startup (lifespan) and stored on app.state. The dependency reads
it from there.

This pattern means:
  - Tests can create a test app with a test engine on app.state
  - The production app uses the real engine
  - No hidden module-level globals that are hard to replace in tests

WHY `yield` INSTEAD OF `return`?
==================================
Using `yield` turns get_session into a "context manager dependency". Code before
`yield` runs before the route handler; code after `yield` (the `finally` block)
runs after the route handler, EVEN IF the handler raised an exception.

This guarantees that the database session is always closed properly, which
returns the connection back to the pool for the next request to use.
"""

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield one AsyncSession per request, always closed on exit.

    FastAPI calls this before the route handler (opens the session)
    and after the route handler (closes it), even if an exception occurred.

    Usage in a route:
        async def my_route(session: AsyncSession = Depends(get_session)):
            ...

    The session is taken from app.state.sessionmaker, which is the
    async_sessionmaker created during application startup (see main.py lifespan).
    """
    # request.app.state.sessionmaker is the async_sessionmaker stored during
    # application startup. Calling it with () creates one new AsyncSession.
    # AsyncSession is NOT thread-safe but IS coroutine-safe - one per request
    # is the correct pattern.
    async with request.app.state.sessionmaker() as session:
        # `yield` hands the session to the route handler.
        # After the handler finishes (or raises), execution resumes here
        # and the `async with` block closes the session.
        yield session
