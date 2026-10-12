# WARDEN Engineering Report: Prompt 2 Identity, Monitors, Tags & SSRF Shield

**Component:** WARDEN Identity & Auth System, Monitors & Tags CRUD, URL Safety Engine (`warden_net`), and SSRF Defense
**Branch:** `fast-api-dev`
**Date:** October 2026
**Status:** All Checkpoints Complete (CP1, CP2, CP3, CP4, CP5) & All Quality Gates Passing

---

## 1. Your TODOs (Student Implementation Stubs)

Per the course engineering rules, two critical security primitives are intentionally stubbed out with `raise NotImplementedError("see docstring")` and 3-level hint ladders. The test suite for these stubs is tagged with `@pytest.mark.student_todo` and excluded from the default `make check` pipeline so that continuous integration remains green, while `make check-todos` provides an honest, failing test harness.

### Stub File Locations & Signatures

1. **Host & IP Address Classification Primitive:**
   - **Path:** [`libs/warden_core/src/warden_core/ssrf.py`](file:///home/sidharth/WARDEN/libs/warden_core/src/warden_core/ssrf.py)
   - **Signatures:**
     ```python
     def parse_ip_literal(host: str) -> IPv4Address | IPv6Address | None: ...
     def is_blocked_ip(ip: IPv4Address | IPv6Address) -> str | None: ...
     ```
   - **Plain-English Specification:**
     - `parse_ip_literal`: Takes a raw host string (e.g., extracted from a URL authority). If the host is a literal IP address represented in ANY format an attacker might construct (standard dotted-decimal `127.0.0.1`, bare 32-bit decimal integer `2130706433`, octal octets `0177.0.0.1`, hex octets `0x7f.0x0.0x0.0x1`, bare hex integer `0x7f000001`, or IPv6 formats including `::1` and IPv4-mapped `::ffff:127.0.0.1`), normalize it and return an `IPv4Address` or `IPv6Address`. If the string is a genuine domain name (e.g. `example.com`), return `None`. Crucially, perform NO DNS resolution.
     - `is_blocked_ip`: Takes a parsed `IPv4Address` or `IPv6Address` and returns a machine-readable rejection string (`"loopback"`, `"cloud_metadata"`, `"link_local"`, `"private"`, `"cgnat"`, `"multicast"`, `"reserved"`, `"unspecified"`) if the IP must never be requested by WARDEN infrastructure, or `None` if the target is safe for public routing. Specifically, `169.254.169.254` (AWS/GCP/Azure IMDS) must return `"cloud_metadata"`, `100.64.0.0/10` (CGNAT) must return `"cgnat"`, and IPv4-mapped IPv6 addresses (`::ffff:x.x.x.x`) must unwrap the embedded IPv4 address and evaluate it recursively.

2. **Redis-Backed Access-Token Denylist Primitive:**
   - **Path:** [`services/api/src/warden_api/security/denylist.py`](file:///home/sidharth/WARDEN/services/api/src/warden_api/security/denylist.py)
   - **Signatures:**
     ```python
     async def add_to_denylist(redis: Redis, jti: str, ttl_seconds: int) -> None: ...
     async def is_denylisted(redis: Redis, jti: str) -> bool: ...
     ```
   - **Plain-English Specification:**
     - `add_to_denylist`: Stores a revoked JWT ID (`jti`) in Redis with an automatic time-to-live (`ttl_seconds`) corresponding to the token's remaining validity window. If `ttl_seconds <= 0`, no write is needed since the token is already expired. Must be idempotent (calling multiple times for the same `jti` is safe).
     - `is_denylisted`: Checks if `jti` exists in the Redis denylist using an `EXISTS` check. Returns `True` if revoked, `False` otherwise.

### Tests Currently Failing Under `make check-todos`

Running `make check-todos` executes 40 tests that fail cleanly with `NotImplementedError`:

```text
FAILED libs/warden_core/tests/test_ssrf_student.py::test_parse_ip_literal_dotted_decimal
FAILED libs/warden_core/tests/test_ssrf_student.py::test_parse_ip_literal_bare_decimal_integer
FAILED libs/warden_core/tests/test_ssrf_student.py::test_parse_ip_literal_octal_and_hex_octets
FAILED libs/warden_core/tests/test_ssrf_student.py::test_parse_ip_literal_ipv6_forms
FAILED libs/warden_core/tests/test_ssrf_student.py::test_parse_ip_literal_regular_hostnames_return_none
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[127.0.0.1-loopback]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[127.0.0.2-loopback]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[127.255.255.254-loopback]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[::1-loopback]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[169.254.169.254-cloud_metadata]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[fd00:ec2::254-cloud_metadata]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[169.254.1.1-link_local]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[169.254.10.20-link_local]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[fe80::1-link_local]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[10.0.0.1-private]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[172.16.0.1-private]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[192.168.1.1-private]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[100.64.0.1-cgnat]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[100.100.100.100-cgnat]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[100.127.255.254-cgnat]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[224.0.0.1-multicast]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[239.255.255.250-multicast]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[ff02::1-multicast]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[0.0.0.0-reserved]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[240.0.0.1-reserved]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[::-unspecified]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[::ffff:127.0.0.1-loopback]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[::ffff:10.0.0.1-private]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_table_driven[::ffff:169.254.169.254-cloud_metadata]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_legitimate_public_ips_allowed[8.8.8.8]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_legitimate_public_ips_allowed[1.1.1.1]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_legitimate_public_ips_allowed[93.184.216.34]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_legitimate_public_ips_allowed[151.101.1.140]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_legitimate_public_ips_allowed[2606:4700:4700::1111]
FAILED libs/warden_core/tests/test_ssrf_student.py::test_is_blocked_ip_hypothesis_oracle_agreement
FAILED libs/warden_net/tests/test_ssrf.py::test_literal_decimal_ip_rejected
FAILED services/api/tests/test_denylist_student.py::test_denylist_add_then_check_is_true
FAILED services/api/tests/test_denylist_student.py::test_denylist_never_added_is_false
FAILED services/api/tests/test_denylist_student.py::test_denylist_ttl_expires
FAILED services/api/tests/test_denylist_student.py::test_denylist_logout_integration_rejects_token
40 failed, 145 deselected, 1 warning in 1.02s
```

---

## 2. Summary of Prompt 2 Additions

Prompt 2 delivers the complete identity, authentication, monitor management, and network security layer for WARDEN:

1. **`warden_net` Library:** Created as a dedicated 4th workspace member (`httpx`, `idna`, `tldextract`, `warden-core`). Its sole mandate is parsing, normalizing (IDNA2008, URL canonicalization, eTLD+1 extraction), and safely fetching user-supplied URLs without any framework dependencies.
2. **Schema & Migration Extensions:** Added 4 new tables via Alembic revision `d7192f44a301`: `tags`, `monitor_tags`, `audit_log`, and `idempotency_keys`. Reconciled `monitors.domain` to store registrable domains (eTLD+1).
3. **Authentication & Identity System:**
   - **Passwords:** Argon2id with production parameters, length bounds (12–128 chars to mitigate hashing DoS), `zxcvbn` complexity checking, and constant-time failure paths via dummy hash verification for nonexistent accounts.
   - **Asymmetric Tokens:** Ed25519 (EdDSA) asymmetric JWT signing with `kid` tracking, strict algorithm allowlisting (`algorithms=["EdDSA"]`), and random `jti` tracking.
   - **Atomic Refresh Token Rotation:** Single-transaction atomic claim (`UPDATE ... WHERE used_at IS NULL RETURNING id`) with automatic parent-child token lineage.
   - **Reuse Detection:** Immediate revocation of entire refresh families upon detecting replayed tokens, generating critical audit log events.
   - **CSRF Double-Submit Protection:** Strict `warden_csrf` cookie paired with `X-CSRF-Token` header verification using constant-time comparison.
   - **Audit Logging:** Structured audit events recorded across all lifecycle actions with client IP and User-Agent metadata.
4. **SSRF Shield & URL Safety:**
   - Canonicalization resolves relative segments, normalizes lowercased schemes and hosts, applies IDNA2008 decoding, sorts query parameters, and strips URL fragments.
   - Multi-address DNS resolution via `getaddrinfo` validating ALL resolved A/AAAA records against blocked network ranges.
   - Safe streaming HTTP client (`safe_get`) with manual redirect loop capping (≤5 hops), re-validation of each redirect target, and streaming byte tracking with instantaneous abort upon exceeding size limits.
5. **Monitors & Tags CRUD API:**
   - Fully featured REST endpoints with keyset pagination (`?limit=&cursor=`), ETag/If-Match optimistic concurrency control, plan quota enforcement (402 Payment Required), and soft deletion (`state = 'deleted'`).
   - Concurrency-safe tag get-or-create using PostgreSQL transaction savepoints (`IntegrityError` resilience).
   - Selector syntax validation for CSS (`cssselect`) and XPath (`lxml.etree.XPath`).
   - Idempotency via `Idempotency-Key` header with SHA-256 request hashing and response replay.
6. **Error Standardization:**
   - RFC 7807 `ProblemDetail` responses across all endpoints and validation failures, stamping each response with `instance = request_id` to correlate client errors directly to structured server log traces.

---

## 3. File-by-File Table

| Path | Purpose | Why |
|---|---|---|
| `libs/warden_net/pyproject.toml` | Workspace package manifest | Declares dependencies (`httpx`, `idna`, `tldextract`, `warden-core`) isolated from web frameworks. |
| `libs/warden_net/src/warden_net/canonicalize.py` | URL canonicalization & eTLD+1 extraction | Normalizes URLs, applies IDNA2008, resolves dots, and extracts registrable domains. |
| `libs/warden_net/src/warden_net/ssrf_check.py` | DNS resolution, SSRF validation, and `safe_get` | Orchestrates IP checks across all A/AAAA records, detects redirect SSRF, and streams with size limits. |
| `libs/warden_net/tests/test_canonicalize.py` | Unit tests for canonicalization | Validates query parameter sorting, default port stripping, IDNA2008 handling, and URL hashing. |
| `libs/warden_net/tests/test_ssrf.py` | Unit tests for SSRF validation & `safe_get` | Tests safe fetches, blocked IP rejections, redirect hop enforcement, and mid-stream byte caps. |
| `libs/warden_core/src/warden_core/ssrf.py` | IP literal parsing and classification stubs | Provides stdlib-only stub signatures and 3-level hint ladders for student implementation. |
| `libs/warden_core/tests/test_ssrf_student.py` | Student test suite for IP classifier | 35 table-driven and Hypothesis property-based tests verifying IP classification. |
| `libs/warden_db/src/warden_db/models.py` | Updated SQLAlchemy ORM models | Adds `Tag`, `MonitorTag`, `AuditLog`, and `IdempotencyKey` entities. |
| `libs/warden_db/migrations/versions/d7192f44a301_tags_audit_idempotency.py` | Alembic revision 2 | Chained migration applying DDL for tags, audit logs, idempotency keys, and indexes. |
| `docs/schema.md` | Schema documentation | Appends table definitions, foreign keys, and indexes for Prompt 2 additions. |
| `scripts/gen_jwt_keys.py` | Standalone Ed25519 key generator | Generates cryptographic PEM keypairs for asymmetric JWT signing without committing secrets. |
| `services/api/src/warden_api/settings.py` | Updated application settings | Adds JWT key paths, TTLs, `env`, and typed `cookie_secure` configuration. |
| `services/api/src/warden_api/errors.py` | RFC 7807 Problem Detail handler | Unifies API errors and validation exceptions into standardized problem+json payloads. |
| `services/api/src/warden_api/security/denylist.py` | Redis access-token denylist stubs | Provides stub signatures and hint ladders for token revocation checking. |
| `services/api/src/warden_api/auth/passwords.py` | Password hashing & complexity validation | Implements Argon2id hashing, `zxcvbn` scoring, and dummy hash timing mitigation. |
| `services/api/src/warden_api/auth/jwt.py` | Ed25519 JWT issue & decode engine | Signs tokens with EdDSA and enforces explicit algorithm verification. |
| `services/api/src/warden_api/auth/tokens.py` | Refresh token lifecycle & rotation | Executes atomic database claims, child token issuance, and reuse revocation. |
| `services/api/src/warden_api/auth/audit.py` | Structured audit logging helper | Records security events in PostgreSQL with client IP and User-Agent extraction. |
| `services/api/src/warden_api/auth/limiter.py` | In-memory sliding rate limiter | Enforces login attempt limits per IP and account with exponential backoff. |
| `services/api/src/warden_api/auth/dependencies.py` | FastAPI auth dependency injection | Resolves current user from JWT Bearer headers and checks denylist state. |
| `services/api/src/warden_api/auth/router.py` | Auth REST API routes | Exposes `/register`, `/login`, `/refresh`, `/logout`, and `/logout-all`. |
| `services/api/src/warden_api/monitors/validators.py` | Selector syntax compilation | Validates CSS (via `cssselect`) and XPath (via `lxml`) syntax trees. |
| `services/api/src/warden_api/monitors/router.py` | Monitors and Tags REST API routes | CRUD endpoints with quota checks, idempotency replay, optimistic locking, and keyset cursors. |
| `services/api/src/warden_api/main.py` | Main application assembly | Registers auth router, monitors router, and RFC 7807 error handlers. |
| `services/api/tests/conftest.py` | Pytest fixtures | Provides `fast_hasher`, `client`, `db_session`, `auth_tokens`, and database mocking. |
| `services/api/tests/test_auth_security.py` | Security-critical auth tests | Verifies alg-confusion rejection, secret logging prevention, timing parity, CSRF, and concurrency. |
| `services/api/tests/test_denylist_student.py` | Student test suite for Redis denylist | 4 tests verifying token blacklisting, expiration, and logout enforcement. |
| `services/api/tests/test_ssrf_corpus.py` | SSRF regression suite | 49 test cases exercising hostile URL patterns, cloud metadata, and legitimate public domains. |
| `services/api/tests/test_monitors_crud.py` | Functional tests for monitors | Validates quotas, idempotency, optimistic concurrency, cross-user isolation, and keyset cursors. |
| `services/api/tests/test_integration.py` | End-to-end integration test | Complete user journey (register, login, CRUD, ETag, soft-delete) against live PostgreSQL and Redis. |
| `Makefile` | Build automation targets | Added `check-todos` target to test student stubs without breaking `make check`. |

---

## 4. Decisions Made and Alternatives Considered

### 1. `warden_net` as an Isolated Workspace Member
* **Decision:** Create `libs/warden_net` as an independent library depending solely on `httpx`, `idna`, `tldextract`, and `warden_core`, with zero dependencies on FastAPI or SQLAlchemy.
* **Alternative Considered:** Placing URL parsing and SSRF checking inside `services/api/src/warden_api`.
* **Trade-off:** On Day 4, the architecture introduces a standalone `fetcher` service operating in the UNTRUSTED network zone (crawling hostile third-party web content without database credentials). Placing URL safety logic inside the API would either force the untrusted fetcher to import from the trusted API service or result in duplicated code. A lightweight library allows both trusted and untrusted tiers to share a single, audited network validation core.

### 2. Asymmetric JWT Signing (Ed25519) vs Symmetric (HS256)
* **Decision:** Sign access tokens with an asymmetric Ed25519 private key, allowing token verification using only the public key.
* **Alternative Considered:** Symmetric signing with HMAC-SHA256 (`HS256`) using a shared secret.
* **Trade-off:** In a distributed architecture where downstream services (e.g., untrusted fetchers or analytics workers) must authenticate requests, symmetric signing requires distributing the signing secret to every verifying service. A breach in any downstream service compromises the entire identity system. With Ed25519, downstream services hold only the public key and can verify signatures without possessing the capability to forge tokens.

### 3. Atomic Refresh Token Rotation (`UPDATE ... WHERE used_at IS NULL RETURNING id`)
* **Decision:** Rotate refresh tokens in a single atomic database statement that claims the old token only if `used_at IS NULL`.
* **Alternative Considered:** Reading the token row (`SELECT`), checking `used_at` in Python, and issuing `UPDATE`.
* **Trade-off:** Under concurrent requests using the same refresh token, Python-level read-then-write checks allow race conditions where two concurrent requests both observe `used_at IS NULL` and both issue new child tokens. The database-level atomic claim guarantees that exactly one transaction succeeds; the competing transaction receives zero updated rows, triggering instant reuse detection and family revocation.

### 4. Quota Enforcement HTTP Status (402 Payment Required vs 403 Forbidden)
* **Decision:** Return `402 Payment Required` when a user attempts to create more monitors than permitted by their subscription plan.
* **Alternative Considered:** Returning `403 Forbidden`.
* **Trade-off:** `403 Forbidden` indicates an authorization failure that cannot be resolved by client action. `402 Payment Required` communicates to API consumers that the requested action is valid under the domain model but exceeds the commercial boundaries of the active tier, prompting an upgrade flow.

### 5. Keyset Cursor Pagination vs Offset Pagination
* **Decision:** Implement keyset pagination (`WHERE (created_at, id) < (:cursor_created_at, :cursor_id) ORDER BY created_at DESC, id DESC`).
* **Alternative Considered:** SQL `LIMIT` and `OFFSET`.
* **Trade-off:** In a change-monitoring system where new monitors are actively registered, offset-based pagination (`OFFSET 20`) causes duplicate or skipped records when rows are inserted between subsequent page fetches. Keyset pagination provides deterministic, non-overlapping traversal with $O(1)$ index seek performance regardless of depth.

### 6. SSRF Error Asymmetry: Detailed Server Logs vs Generic Client Responses
* **Decision:** Server-side logs record the exact blocked IP category and resolved IP addresses, while the API client receives a generic message: `"This URL cannot be monitored"`.
* **Alternative Considered:** Returning the specific failure reason (e.g., `"Resolved to private IP 10.0.1.5"`) in the client JSON response.
* **Trade-off:** Returning detailed network topology errors to unauthenticated or external users turns the monitor validation endpoint into an internal network port and IP scanner (oracle attack). Generic client responses eliminate information leakage while preserving full observability in internal logs.

---

## 5. Auth Contract for the Frontend

This contract specifies the exact network interface for frontend client developers (Day 14):

### 1. Cookies Issued

All cookies are issued with `SameSite=Strict`. In production (`WARDEN_ENV=prod`), they are issued with `Secure=True`. In local development (`WARDEN_ENV=dev`), `Secure=False` is permitted over plain HTTP.

| Cookie Name | Flags | Scope | Purpose |
|---|---|---|---|
| `warden_refresh` | `HttpOnly`, `SameSite=Strict`, `Secure` | `Path=/api/auth` | Holds the raw refresh token string. Inaccessible to JavaScript (XSS-safe). |
| `warden_csrf` | `SameSite=Strict`, `Secure` (NOT HttpOnly) | `Path=/` | Holds a random CSRF double-submit token readable by JavaScript. |

### 2. Request Headers Required

- **Protected API Endpoints (`/api/monitors/*`, `/api/tags`):**
  - `Authorization: Bearer <access_token>`
- **Mutation Endpoints on Auth (`POST /api/auth/refresh`, `POST /api/auth/logout`, `POST /api/auth/logout-all`):**
  - Must include header `X-CSRF-Token: <value_of_warden_csrf_cookie>`.
- **Monitor Creation (`POST /api/monitors`):**
  - Optional `Idempotency-Key: <unique_string>` (replays identical response on duplicate submission).
- **Monitor Mutations (`PATCH /api/monitors/{id}`, `DELETE /api/monitors/{id}`):**
  - Required `If-Match: "<version>"` (matches the `ETag` header returned by `GET` or `POST`).

### 3. Response Shapes

#### Successful Registration (`POST /api/auth/register`) -> `201 Created`
```json
{
  "id": "c1f7a24e-4b21-4f38-9562-b13c38fa3152",
  "email": "user@example.com",
  "role": "user",
  "plan": "free",
  "created_at": "2026-10-12T07:38:19.860941Z"
}
```

#### Successful Login (`POST /api/auth/login`) -> `200 OK`
```json
{
  "access_token": "<jwt_access_token_string>",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {
    "id": "c1f7a24e-4b21-4f38-9562-b13c38fa3152",
    "email": "user@example.com",
    "role": "user",
    "plan": "free"
  }
}
```
*Note:* Browser automatically receives `Set-Cookie: warden_refresh=...; HttpOnly; Path=/api/auth` and `Set-Cookie: warden_csrf=...; Path=/`. The frontend stores `access_token` in memory (not `localStorage`).

#### Successful Token Refresh (`POST /api/auth/refresh`) -> `200 OK`
*Requires:* Cookie `warden_refresh` and header `X-CSRF-Token: <warden_csrf>`.
```json
{
  "access_token": "<jwt_access_token_string>",
  "token_type": "bearer",
  "expires_in": 900
}
```

#### Error Response (RFC 7807 `application/problem+json`)
```json
{
  "type": "about:blank",
  "title": "Unprocessable Entity",
  "status": 422,
  "detail": "Password is too weak: Add another word or two. Uncommon words are better.",
  "instance": "e09ec7ad704d43848f69470449b2385e"
}
```

---

## 6. Deviations from Spec and Conflicts Identified

1. **PyJWT Ed25519 Public-Key-as-HMAC Attack Simulation:**
   - *Observation:* PyJWT strictly validates key formats before encoding; passing an Ed25519 PEM public key to `jwt.encode(..., algorithm="HS256")` raises an immediate `InvalidKeyError` inside the library rather than producing a token.
   - *Resolution:* To faithfully test the server's vulnerability to public-key-as-HMAC confusion attacks, the test suite manually constructs the hostile JWT using Python's stdlib `hmac`, `hashlib.sha256`, and base64 encoding. The test verifies that `get_current_user` rejects this token with `401 Unauthorized` because the server enforces `algorithms=["EdDSA"]`.
2. **`plans` Seed Data Minimum Interval:**
   - *Observation:* In Prompt 1 seed data, the `free` tier defines `min_interval_s = 3600` (1 hour).
   - *Resolution:* Monitor creation validation calculates `effective_min = max(plan.min_interval_s, 30)`. For free tier users, intervals below 3600 seconds are rejected with 422. Test fixtures creating monitors under the free tier use intervals $\ge 3600$.
3. **Pydantic Settings `cookie_secure` Validator:**
   - *Observation:* `cookie_secure` defaults to `True`, but in development environments (`WARDEN_ENV=dev`), browsers connecting over plain HTTP reject `Secure` cookies.
   - *Resolution:* Implemented a Pydantic `model_validator` on `APISettings` that sets `cookie_secure = False` when `env == "dev"` unless an explicit override was configured.

---

## 7. Things Verified in Docs/Source vs. Assumed

1. **IDNA2008 Standard Compliance:**
   - *Verified:* Python's built-in `str.encode('idna')` implements the obsolete IDNA2003 standard, which incorrectly normalizes German sharp S (`ß` to `ss`) and Greek final sigma. Verified that the external `idna` package conforms to IDNA2008 (RFC 5891).
2. **`tldextract` Registrable Domain Extraction:**
   - *Verified:* `tldextract.extract(url).registered_domain` correctly parses multi-level public suffixes (e.g., `shop.example.co.uk` yields `example.co.uk` rather than `co.uk`).
3. **SQLAlchemy 2.0 Savepoints for Concurrency Races:**
   - *Verified:* Catching an `IntegrityError` directly in an outer transaction poisons the entire PostgreSQL transaction block (`InFailedSqlTransaction`). Wrapped tag get-or-create inside `async with session.begin_nested():` to catch race collisions on unique constraints cleanly.
4. **Streaming Byte Counters in `httpx`:**
   - *Verified:* Consuming response streams with `response.aiter_bytes()` tracks cumulative transferred byte volume and allows aborting requests instantly when `max_bytes` is exceeded without buffering unbounded payloads into memory.

---

## 8. Exact Verification Commands & Execution Logs

### 1. Golden Quality Gate (`make check`)
Runs linting, formatting checks, MyPy strict type checking, and all production tests:

```bash
$ make check
# Check formatting and lint rules WITHOUT making any changes.
# Fails with a non-zero exit code if anything is wrong (used in CI).
uv run ruff check .
All checks passed!
uv run ruff format --check .
52 files already formatted
# Run mypy static type checker on all source directories.
# strict mode: no Any, no missing return types, no untyped functions.
uv run mypy libs/warden_core/src libs/warden_db/src libs/warden_net/src services/api/src
Success: no issues found in 31 source files
# Run all tests (unit + integration if Postgres is reachable).
# -q: quiet mode (one dot per test instead of full test name)
uv run pytest -q
........................................................................ [ 58%]
...................................................                      [100%]
123 passed, 62 deselected in 2.74s
```

### 2. Student TODO Verification (`make check-todos`)
Runs all tests marked `@pytest.mark.student_todo` against the stubs:

```bash
$ make check-todos
uv run pytest -q -m student_todo
40 failed, 145 deselected in 1.02s
```

### 3. Full Database & Redis Integration Test
Runs against the live Docker Compose services on `127.0.0.1:5433` and `127.0.0.1:6379`:

```bash
$ WARDEN_DATABASE_URL="postgresql+asyncpg://warden:Flame%402005Kaiser@127.0.0.1:5433/warden" uv run pytest -m integration -v
============================= test session starts ==============================
collected 185 items / 163 deselected / 22 selected

libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_alembic_version_matches_head PASSED [  4%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[audit_log] PASSED [  9%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[change_events] PASSED [ 13%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[feedback_labels] PASSED [ 18%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[idempotency_keys] PASSED [ 22%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[monitor_tags] PASSED [ 27%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[monitors] PASSED [ 31%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[outbox] PASSED [ 36%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[plans] PASSED [ 40%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[refresh_families] PASSED [ 45%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[refresh_tokens] PASSED [ 50%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[snapshots] PASSED [ 54%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[tags] PASSED [ 59%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_each_table_exists_in_db[users] PASSED [ 63%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_citext_extension_installed PASSED [ 68%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_email_column_is_citext PASSED [ 72%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_partial_indexes_exist PASSED [ 77%]
libs/warden_db/tests/test_migrations.py::TestMigrationSchema::test_plans_seed_data_present PASSED [ 81%]
libs/warden_db/tests/test_migrations.py::TestDbRoundTrip::test_insert_plan_and_user PASSED [ 86%]
libs/warden_db/tests/test_migrations.py::TestDbRoundTrip::test_citext_case_insensitive_lookup PASSED [ 90%]
libs/warden_db/tests/test_migrations.py::TestScratchDatabaseLifecycle::test_scratch_database_migration_lifecycle PASSED [ 95%]
services/api/tests/test_integration.py::test_full_user_lifecycle_integration PASSED [100%]

================ 22 passed, 163 deselected, 2 warnings in 1.76s ================
```

---

## 9. Known Limitations & Roadmap

1. **Idempotency Keys TTL & Cleanup:**
   - *Limitation:* The `idempotency_keys` table records request hashes and responses indefinitely. Without automated pruning, high-throughput environments will accumulate dead rows.
   - *Roadmap:* Scheduled for background partition maintenance or Redis offloading in Day 8/17.
2. **In-Memory Rate Limiting Scope:**
   - *Limitation:* The login sliding-window limiter stores state in process memory. In multi-replica deployments, requests routed across different instances do not share counter state.
   - *Roadmap:* To be replaced by the distributed Redis GCRA (Generic Cell Rate Algorithm) rate limiter on Day 8.
3. **Stateless Access Token Revocation Window on `logout-all`:**
   - *Limitation:* `logout-all` revokes all refresh token families in PostgreSQL, but previously issued access tokens remain valid until their $\le 15$-minute natural expiration. Per-user revocation timestamps or global user version numbers are not yet checked on every request to avoid per-request database lookups.
   - *Trade-off:* Acceptable security trade-off given the short 15-minute token TTL.
4. **Regular Expression Selector ReDoS Mitigation:**
   - *Limitation:* Regex selectors are validated using Python's stdlib `re.compile()`. Highly pathological regular expressions (e.g. catastrophic backtracking patterns) can cause CPU starvation during execution.
   - *Roadmap:* Hardened linear-time regex execution via Google RE2 is scheduled for Day 16.

---

## 10. Ten Interviewer Questions & Answers

### Q1: Why use Ed25519 (EdDSA) instead of RSA or HMAC-SHA256 for signing access tokens?
**Answer:** Ed25519 provides asymmetric cryptography with 128-bit security using compact 32-byte public keys and 64-byte signatures, computing orders of magnitude faster than RSA-2048 with constant-time execution resistant to cache-timing attacks. More importantly, asymmetric signing allows untrusted downstream microservices (such as our Day 4 fetchers) to verify tokens using only the public key. With symmetric algorithms like HS256, every verifying service must hold the signing secret, meaning any compromised worker could forge admin tokens.

### Q2: What is the JWT Algorithm Confusion Attack, and how does WARDEN prevent it?
**Answer:** In an algorithm confusion attack, an attacker takes a server's public key (e.g., an RSA or Ed25519 public key) and uses it as the secret key to sign a token with a symmetric algorithm like `HS256`. If the verification server simply reads the `alg` header from the token and verifies using its configured key object, it treats the public key bytes as an HMAC secret and accepts the forged signature. WARDEN prevents this by explicitly passing `algorithms=["EdDSA"]` to the decode call, completely ignoring the token's incoming `alg` header claim.

### Q3: How does WARDEN prevent race conditions during refresh token rotation?
**Answer:** Refresh token rotation uses a single atomic database statement: `UPDATE refresh_tokens SET used_at = now() WHERE id = :id AND used_at IS NULL RETURNING id`. Under high concurrency, PostgreSQL's row-level locking guarantees that only one transaction can successfully update the row and receive an ID back. If a second concurrent request attempts the same claim, zero rows are updated. The application detects this failure, flags a token reuse attempt, and immediately revokes the entire token family.

### Q4: Why does WARDEN enforce a maximum password length of 128 characters?
**Answer:** Argon2id is computationally expensive by design. If an API accepts arbitrary-length input (e.g., a 10MB string), an attacker can flood the authentication endpoint with giant passwords. The server spends massive CPU cycles feeding megabytes into the key derivation function, resulting in an unauthenticated Denial of Service (DoS) attack. Capping passwords at 128 characters prevents algorithmic complexity exhaustion while providing ample entropy for legitimate passphrases.

### Q5: Why is the SSRF check split between creation time and fetch time?
**Answer:** Performing an SSRF check at monitor creation provides immediate feedback to users who accidentally submit private or invalid URLs. However, this check is strictly a convenience validation and cannot guarantee security at fetch time due to Time-of-Check to Time-of-Use (TOCTOU) vulnerabilities: an attacker can register a domain that resolves to a public IP during creation, and later modify the DNS A record to point to `169.254.169.254` (DNS rebinding). Therefore, the fetch engine must re-validate the target IP address immediately prior to opening every network socket.

### Q6: Why must the HTTP client disable automatic redirect following (`follow_redirects=False`)?
**Answer:** If an HTTP client follows redirects automatically, an attacker can supply a public URL that returns a `302 Found` with `Location: http://169.254.169.254/latest/meta-data/`. The client would fetch the initial safe URL, pass initial validation, and then transparently fetch the protected internal endpoint. WARDEN disables automatic redirects, manually inspects each 3xx response, re-runs full canonicalization and SSRF validation on the destination URL, and caps chains at 5 hops.

### Q7: Why use `savepoint` (`begin_nested()`) when inserting tags under high concurrency?
**Answer:** In PostgreSQL, if an insert violates a `UNIQUE(user_id, name)` constraint, the entire transaction block enters an aborted state (`InFailedSqlTransaction`), preventing any subsequent queries within the same transaction. By wrapping the insert in `session.begin_nested()`, PostgreSQL creates an internal `SAVEPOINT`. If an `IntegrityError` occurs due to a concurrent insert, the savepoint rolls back only the failed insert without corrupting the parent transaction, allowing the code to query and return the concurrently inserted tag.

### Q8: What vulnerability is introduced if an API returns detailed SSRF failure reasons to the client?
**Answer:** If the API returns specific error reasons such as `"IP 10.0.1.5 is a blocked private address"`, an attacker can submit URLs containing internal hostnames or IP ranges and use the error messages to map the internal network topology, identify active internal IP addresses, and deduce subnet boundaries. Returning a generic `"This URL cannot be monitored"` message prevents the API from acting as an intranet port scanning oracle.

### Q9: Why is IDNA2008 required over Python's built-in IDNA codec?
**Answer:** Python's built-in `.encode('idna')` implements the obsolete IDNA2003 standard (RFC 3490). IDNA2003 applies string preparation mapping that alters certain characters—for example, mapping the German sharp S (`ß`) to `ss` and stripping zero-width joiners. This causes domains like `faß.de` to resolve to `fass.de`, which may be registered to a different entity. IDNA2008 (RFC 5895), provided by the `idna` package, preserves these distinct characters and prevents domain spoofing and homograph vulnerabilities.

### Q10: Why does the API return 404 Not Found instead of 403 Forbidden when a user requests a monitor owned by someone else?
**Answer:** Returning `403 Forbidden` confirms to an attacker that the resource ID exists, allowing resource enumeration attacks across the user base. Querying with `WHERE id = :id AND user_id = :user_id` returns `404 Not Found` when the record does not match the requester's ownership, ensuring that unauthorized users cannot distinguish between non-existent IDs and IDs belonging to other tenants.
