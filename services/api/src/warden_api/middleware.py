"""Per-request middleware: request ID injection and structured access logging.

WHAT IS MIDDLEWARE?
===================
Middleware is code that runs on EVERY request, before your route handler is
called and after it returns. Think of it as a wrapper around all your endpoints.
This middleware does two things for every single request:

  1. REQUEST ID: Assigns a unique ID to the request (e.g. "a3f2b1c4...").
     This ID appears in every log line for that request, so when something
     goes wrong, you can search your logs by request ID and see the full
     picture of what happened.

  2. ACCESS LOG: Logs one line per request containing: the method (GET/POST),
     the path (/health), the status code (200/503), and how long it took (ms).

WHY CONTEXTVARS INSTEAD OF THREAD-LOCAL STORAGE?
=================================================
In a traditional synchronous web server (like old Django or Flask with gunicorn),
each HTTP request runs in its own OS thread. Thread-local storage (threading.local)
works because thread A's data is completely isolated from thread B's data.

FastAPI is ASYNC. A single OS thread can handle thousands of requests at once
using Python's asyncio event loop. The event loop switches between requests at
every `await` point. If we used thread-locals, request A's data would
overwrite request B's data because they share the same thread.

contextvars (Python 3.7+) are the async-safe replacement. Each asyncio Task
(which represents one request) gets its own copy of context variables. When
the event loop switches from Task A to Task B, the context variables switch
too - automatically, at zero cost, built into Python.

HOW STRUCTLOG CONTEXTVARS WORK:
================================
structlog.contextvars stores key-value pairs (like request_id, method, path)
that get automatically merged into EVERY log line produced during that request.
So if a function deep inside your code logs something, the request_id appears
automatically without passing it as a parameter.

clear_contextvars()       - wipe any leftover data from the previous request
bind_contextvars(k=v)     - attach key-value pairs to the current request's context
merge_contextvars          - processor in logconfig.py that merges them into every log line
"""

import re
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from structlog.contextvars import bind_contextvars, clear_contextvars

# Pre-compile the regex once at module load time, not on every request.
# This is a minor performance optimization - regex compilation is not free.
# Pattern: only allow alphanumeric characters and hyphens, 1-64 chars long.
# WHY this validation? An attacker could send a crafted x-request-id that
# contains log injection payloads (newlines, quotes) or is extremely long.
# We only accept IDs that look like UUIDs or short alphanumeric strings.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,64}$")

# Module-level structlog logger. This is the ONE allowed module-level "global"
# per the project spec because loggers are immutable and thread/async-safe.
_log = structlog.get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware that assigns a unique ID to every request and logs it.

    What BaseHTTPMiddleware does:
      Starlette's BaseHTTPMiddleware calls our `dispatch()` method for every
      request. We call `await call_next(request)` to run the actual route
      handler, then we can inspect/modify the response before returning it.
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:
        """Process one HTTP request: add ID, log it, measure time."""
        # ----------------------------------------------------------------
        # Step 1: Clear any leftover context from the previous request.
        # ----------------------------------------------------------------
        # WHY? In async code, if an exception occurs before we clear context,
        # the next request might inherit the previous request's context variables.
        # Clearing first guarantees a clean slate for every request.
        clear_contextvars()

        # ----------------------------------------------------------------
        # Step 2: Determine the request ID.
        # ----------------------------------------------------------------
        # Check if the client sent us a request ID (e.g. from a frontend or
        # another service for distributed tracing). Only accept it if it
        # passes our safety validation - otherwise generate a fresh one.
        incoming_id = request.headers.get("x-request-id", "")
        if incoming_id and _REQUEST_ID_RE.match(incoming_id):
            # Client's ID is safe to use - helps with distributed tracing
            # where the caller already has a request ID they want to track.
            request_id = incoming_id
        else:
            # Generate a new ID: uuid4 gives 128 bits of randomness.
            # .hex removes hyphens: "a3f2b1c4d5e6f7a8b9c0d1e2f3a4b5c6" (32 chars)
            request_id = uuid.uuid4().hex

        # ----------------------------------------------------------------
        # Step 3: Bind context variables for this request.
        # ----------------------------------------------------------------
        # Every structlog log call in this request will automatically include
        # these fields. This happens because logconfig.py includes
        # structlog.contextvars.merge_contextvars in the processor chain.
        bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        # ----------------------------------------------------------------
        # Step 4: Run the actual route handler and measure how long it takes.
        # ----------------------------------------------------------------
        start = time.perf_counter()

        # call_next is typed as object by BaseHTTPMiddleware's signature but
        # is actually a callable. We annotate it properly here.
        import typing

        response: Response = await typing.cast(
            typing.Callable[[Request], typing.Awaitable[Response]], call_next
        )(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        # ----------------------------------------------------------------
        # Step 5: Log one access line per request.
        # ----------------------------------------------------------------
        # The request_id/method/path are already in the context (step 3),
        # so they appear automatically in this log line too.
        _log.info(
            "request",
            status=response.status_code,
            duration_ms=duration_ms,
        )

        # ----------------------------------------------------------------
        # Step 6: Add the request ID to the response headers.
        # ----------------------------------------------------------------
        # Returning the request_id in the response lets the client correlate
        # what they sent with what appears in our logs. Standard practice in
        # APIs (used by AWS, Stripe, GitHub, etc.)
        response.headers["x-request-id"] = request_id
        return response
