"""Monitors management API router."""

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from warden_api.auth.audit import log_audit_event
from warden_api.auth.dependencies import get_current_user
from warden_api.deps import get_db
from warden_api.errors import APIError
from warden_api.monitors.validators import validate_selector_config
from warden_db.models import (
    IdempotencyKey,
    Monitor,
    Plan,
    Tag,
    User,
)
from warden_net.canonicalize import canonicalize_url
from warden_net.ssrf_check import validate_url_is_safe

_log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["monitors"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateMonitorRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    min_interval_s: int = Field(default=3600, ge=30, le=2592000)
    max_interval_s: int = Field(default=86400, ge=30, le=2592000)
    selector_config: dict[str, Any] | None = None
    needs_render: bool = False
    rule_src: str | None = None
    tags: list[str] | None = Field(default=None, max_length=10)


class UpdateMonitorRequest(BaseModel):
    min_interval_s: int | None = Field(default=None, ge=30, le=2592000)
    max_interval_s: int | None = Field(default=None, ge=30, le=2592000)
    state: str | None = Field(default=None, pattern="^(active|paused)$")
    selector_config: dict[str, Any] | None = None
    needs_render: bool | None = None
    rule_src: str | None = None
    tags: list[str] | None = Field(default=None, max_length=10)


class TagResponse(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreateTagRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class MonitorResponse(BaseModel):
    id: uuid.UUID
    url: str
    url_canonical: str
    domain: str
    state: str
    min_interval_s: int
    max_interval_s: int
    needs_render: bool
    selector_config: dict[str, Any] | None
    rule_src: str | None
    version: int
    created_at: datetime
    tags: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class MonitorListResponse(BaseModel):
    items: list[MonitorResponse]
    next_cursor: str | None
    has_more: bool


# ---------------------------------------------------------------------------
# Tag Get-or-Create Helper
# ---------------------------------------------------------------------------


async def _get_or_create_tag(session: AsyncSession, user_id: uuid.UUID, tag_name: str) -> Tag:
    """Get or create a tag for a user, safely handling concurrent creation races."""
    clean_name = tag_name.strip()
    if not (1 <= len(clean_name) <= 50):
        raise APIError(
            status_code=422,
            title="Validation Error",
            detail=f"Tag name '{clean_name}' must be between 1 and 50 characters",
        )

    # Use a savepoint to catch duplicate race conditions without poisoning transaction
    async with session.begin_nested():
        stmt = select(Tag).where(Tag.user_id == user_id, Tag.name == clean_name)
        res = await session.execute(stmt)
        tag = res.scalar_one_or_none()
        if tag is not None:
            return tag

        try:
            new_tag = Tag(user_id=user_id, name=clean_name)
            session.add(new_tag)
            await session.flush()
            return new_tag
        except IntegrityError:
            # Another concurrent request inserted the same tag; re-fetch
            pass

    # Re-query outside the nested savepoint
    stmt = select(Tag).where(Tag.user_id == user_id, Tag.name == clean_name)
    res = await session.execute(stmt)
    tag = res.scalar_one_or_none()
    if tag is None:
        raise APIError(
            status_code=500,
            title="Internal Error",
            detail="Failed to get or create tag",
        )
    return tag


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/monitors", response_model=MonitorResponse, status_code=201)
async def create_monitor(
    body: CreateMonitorRequest,
    request: Request,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    """Create a new monitor with SSRF check, intervals, quota, and idempotency."""
    # --------------------------------------------------------------------------
    # 1. Idempotency Check
    # --------------------------------------------------------------------------
    body_dict = body.model_dump(mode="json")
    req_hash_input = f"POST:{request.url.path}:{json.dumps(body_dict, sort_keys=True)}"
    request_hash = hashlib.sha256(req_hash_input.encode("utf-8")).hexdigest()

    if idempotency_key:
        stmt_idem = select(IdempotencyKey).where(
            IdempotencyKey.key == idempotency_key,
            IdempotencyKey.user_id == user.id,
        )
        res_idem = await session.execute(stmt_idem)
        existing_key = res_idem.scalar_one_or_none()

        if existing_key:
            if existing_key.request_hash == request_hash:
                # Replay stored response verbatim
                response.status_code = existing_key.response_status
                return existing_key.response_body
            raise APIError(
                status_code=422,
                title="Unprocessable Entity",
                detail="Idempotency-Key reused with a different request body",
            )

    # --------------------------------------------------------------------------
    # 2. Canonicalization and Creation-time SSRF Validation
    # --------------------------------------------------------------------------
    try:
        canonical_obj = canonicalize_url(body.url)
    except ValueError as exc:
        raise APIError(
            status_code=422,
            title="Unprocessable Entity",
            detail=f"URL validation failed: {exc}",
        ) from exc

    # CREATION-TIME SSRF CHECK NOTE:
    # This check is an early convenience rejection. It is NOT a substitute for
    # fetch-time validation (Prompt 3/4) because DNS records may change (TOCTOU gap).
    # INFORMATION LEAKAGE NOTE:
    # We log detailed reasons server-side, but return a generic response to the client
    # so WARDEN cannot be used as an internal network probe.
    ssrf_check = await validate_url_is_safe(canonical_obj.url)
    if not ssrf_check.safe:
        _log.warning(
            "ssrf_rejected_on_monitor_creation",
            user_id=str(user.id),
            url=body.url,
            reason=ssrf_check.reason,
            resolved_ips=ssrf_check.resolved_ips,
        )
        raise APIError(
            status_code=422,
            title="Unprocessable Entity",
            detail="This URL cannot be monitored",
        )

    # --------------------------------------------------------------------------
    # 3. Plan & Quota Validation
    # --------------------------------------------------------------------------
    plan_stmt = select(Plan).where(Plan.code == user.plan)
    plan_res = await session.execute(plan_stmt)
    user_plan = plan_res.scalar_one_or_none()
    if not user_plan:
        raise APIError(status_code=500, title="Internal Error", detail="User plan not found")

    # Intervals validation
    effective_min = max(user_plan.min_interval_s, 30)
    if body.min_interval_s < effective_min or body.min_interval_s > 2592000:
        raise APIError(
            status_code=422,
            title="Unprocessable Entity",
            detail=f"min_interval_s must be between {effective_min} and 2592000 seconds",
        )
    if body.max_interval_s < body.min_interval_s or body.max_interval_s > 2592000:
        raise APIError(
            status_code=422,
            title="Unprocessable Entity",
            detail="max_interval_s must be >= min_interval_s and <= 2592000",
        )

    # Quota check (HTTP 402 Payment Required: standard for subscription limits)
    count_stmt = select(func.count(Monitor.id)).where(
        Monitor.user_id == user.id,
        Monitor.state != "deleted",
    )
    count_res = await session.execute(count_stmt)
    current_count = count_res.scalar_one()

    if current_count >= user_plan.max_monitors:
        raise APIError(
            status_code=402,
            title="Payment Required",
            detail=(
                f"Plan monitor limit reached ({user_plan.max_monitors} monitors). "
                "Upgrade plan to add more."
            ),
        )

    # --------------------------------------------------------------------------
    # 4. Selector Configuration Validation
    # --------------------------------------------------------------------------
    validate_selector_config(body.selector_config)

    # --------------------------------------------------------------------------
    # 5. Tag Associations
    # --------------------------------------------------------------------------
    tag_objs: list[Tag] = []
    if body.tags:
        for tag_name in body.tags:
            tag_objs.append(await _get_or_create_tag(session, user.id, tag_name))

    # --------------------------------------------------------------------------
    # 6. Insert Monitor
    # --------------------------------------------------------------------------
    now = datetime.now(UTC)
    monitor = Monitor(
        user_id=user.id,
        url=body.url,
        url_canonical=canonical_obj.url,
        url_hash=canonical_obj.url_hash,
        domain=canonical_obj.registrable_domain,
        min_interval_s=body.min_interval_s,
        max_interval_s=body.max_interval_s,
        next_due_at=now,
        state="active",
        version=1,
        needs_render=body.needs_render,
        rule_src=body.rule_src,
        selector_config=body.selector_config,
    )
    monitor.tags = tag_objs
    session.add(monitor)
    await session.flush()

    # Build response data
    resp_obj = MonitorResponse(
        id=monitor.id,
        url=monitor.url,
        url_canonical=monitor.url_canonical,
        domain=monitor.domain,
        state=monitor.state,
        min_interval_s=monitor.min_interval_s,
        max_interval_s=monitor.max_interval_s,
        needs_render=monitor.needs_render,
        selector_config=monitor.selector_config,
        rule_src=monitor.rule_src,
        version=monitor.version,
        created_at=monitor.created_at,
        tags=[t.name for t in tag_objs],
    )
    resp_data = resp_obj.model_dump(mode="json")

    # Store idempotency record if requested
    if idempotency_key:
        idem_entry = IdempotencyKey(
            key=idempotency_key,
            user_id=user.id,
            request_hash=request_hash,
            response_status=201,
            response_body=resp_data,
        )
        session.add(idem_entry)
        await session.flush()

    await log_audit_event(
        session,
        action="monitor_created",
        user_id=user.id,
        resource_type="monitor",
        resource_id=str(monitor.id),
        request=request,
    )
    await session.commit()

    response.headers["ETag"] = f'"{monitor.version}"'
    return resp_obj


@router.get("/monitors", response_model=MonitorListResponse)
async def list_monitors(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    domain: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
) -> MonitorListResponse:
    """List monitors using keyset pagination (ORDER BY created_at DESC, id DESC)."""
    # Parse cursor if present: base64("{created_at_iso}|{id}")
    cursor_created_at: datetime | None = None
    cursor_id: uuid.UUID | None = None

    if cursor:
        try:
            decoded = base64.b64decode(cursor.encode("ascii")).decode("utf-8")
            iso_str, id_str = decoded.split("|")
            cursor_created_at = datetime.fromisoformat(iso_str)
            cursor_id = uuid.UUID(id_str)
        except Exception as exc:
            raise APIError(
                status_code=400,
                title="Bad Request",
                detail="Malformed pagination cursor",
            ) from exc

    # Base query with selectinload for tags
    query = select(Monitor).options(selectinload(Monitor.tags)).where(Monitor.user_id == user.id)

    # State filter: default exclude 'deleted' unless explicitly queried
    if state:
        query = query.where(Monitor.state == state)
    else:
        query = query.where(Monitor.state != "deleted")

    if domain:
        query = query.where(Monitor.domain == domain.lower().strip())

    if tag:
        query = query.join(Monitor.tags).where(Tag.name == tag.strip())

    # Keyset predicate: (created_at, id) < (cursor_created_at, cursor_id)
    if cursor_created_at and cursor_id:
        query = query.where(
            (Monitor.created_at < cursor_created_at)
            | ((Monitor.created_at == cursor_created_at) & (Monitor.id < cursor_id))
        )

    # Order and limit + 1 to detect has_more
    query = query.order_by(Monitor.created_at.desc(), Monitor.id.desc()).limit(limit + 1)
    res = await session.execute(query)
    rows = list(res.scalars().all())

    has_more = len(rows) > limit
    items = rows[:limit]

    next_cursor = None
    if has_more and items:
        last_item = items[-1]
        raw_cursor = f"{last_item.created_at.isoformat()}|{last_item.id}"
        next_cursor = base64.b64encode(raw_cursor.encode("utf-8")).decode("ascii")

    response_items = [
        MonitorResponse(
            id=m.id,
            url=m.url,
            url_canonical=m.url_canonical,
            domain=m.domain,
            state=m.state,
            min_interval_s=m.min_interval_s,
            max_interval_s=m.max_interval_s,
            needs_render=m.needs_render,
            selector_config=m.selector_config,
            rule_src=m.rule_src,
            version=m.version,
            created_at=m.created_at,
            tags=[t.name for t in m.tags],
        )
        for m in items
    ]

    return MonitorListResponse(
        items=response_items,
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/monitors/{monitor_id}", response_model=MonitorResponse)
async def get_monitor(
    monitor_id: uuid.UUID,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MonitorResponse:
    """Retrieve monitor by ID, scoped strictly to the authenticated user."""
    stmt = (
        select(Monitor)
        .options(selectinload(Monitor.tags))
        .where(Monitor.id == monitor_id, Monitor.user_id == user.id)
    )
    res = await session.execute(stmt)
    monitor = res.scalar_one_or_none()

    # Never return 403 on cross-user access; return 404 to avoid revealing existence
    if not monitor:
        raise APIError(
            status_code=404,
            title="Not Found",
            detail="Monitor not found",
        )

    response.headers["ETag"] = f'"{monitor.version}"'
    return MonitorResponse(
        id=monitor.id,
        url=monitor.url,
        url_canonical=monitor.url_canonical,
        domain=monitor.domain,
        state=monitor.state,
        min_interval_s=monitor.min_interval_s,
        max_interval_s=monitor.max_interval_s,
        needs_render=monitor.needs_render,
        selector_config=monitor.selector_config,
        rule_src=monitor.rule_src,
        version=monitor.version,
        created_at=monitor.created_at,
        tags=[t.name for t in monitor.tags],
    )


@router.patch("/monitors/{monitor_id}", response_model=MonitorResponse)
async def update_monitor(
    monitor_id: uuid.UUID,
    body: UpdateMonitorRequest,
    request: Request,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> MonitorResponse:
    """Update monitor fields using optimistic concurrency with If-Match version matching."""
    if not if_match:
        raise APIError(
            status_code=400,
            title="Bad Request",
            detail="If-Match header is required for updates",
        )

    stmt = (
        select(Monitor)
        .options(selectinload(Monitor.tags))
        .where(Monitor.id == monitor_id, Monitor.user_id == user.id)
    )
    res = await session.execute(stmt)
    monitor = res.scalar_one_or_none()

    if not monitor:
        raise APIError(status_code=404, title="Not Found", detail="Monitor not found")

    clean_version = if_match.strip('"')
    if str(monitor.version) != clean_version:
        raise APIError(
            status_code=412,
            title="Precondition Failed",
            detail=f"Version mismatch: current monitor version is {monitor.version}",
        )

    # Validate updates
    if body.min_interval_s is not None:
        if body.min_interval_s < 30 or body.min_interval_s > 2592000:
            raise APIError(
                status_code=422,
                title="Validation Error",
                detail="min_interval_s out of range",
            )
        monitor.min_interval_s = body.min_interval_s

    if body.max_interval_s is not None:
        if body.max_interval_s < monitor.min_interval_s or body.max_interval_s > 2592000:
            raise APIError(
                status_code=422,
                title="Validation Error",
                detail="max_interval_s invalid",
            )
        monitor.max_interval_s = body.max_interval_s

    if body.state is not None:
        monitor.state = body.state

    if body.needs_render is not None:
        monitor.needs_render = body.needs_render

    if body.rule_src is not None:
        monitor.rule_src = body.rule_src

    if body.selector_config is not None:
        validate_selector_config(body.selector_config)
        monitor.selector_config = body.selector_config

    if body.tags is not None:
        tag_objs: list[Tag] = []
        for tag_name in body.tags:
            tag_objs.append(await _get_or_create_tag(session, user.id, tag_name))
        monitor.tags = tag_objs

    # Increment optimistic concurrency version
    monitor.version += 1
    await session.flush()

    await log_audit_event(
        session,
        action="monitor_updated",
        user_id=user.id,
        resource_type="monitor",
        resource_id=str(monitor.id),
        metadata={"new_version": monitor.version},
        request=request,
    )
    await session.commit()

    response.headers["ETag"] = f'"{monitor.version}"'
    return MonitorResponse(
        id=monitor.id,
        url=monitor.url,
        url_canonical=monitor.url_canonical,
        domain=monitor.domain,
        state=monitor.state,
        min_interval_s=monitor.min_interval_s,
        max_interval_s=monitor.max_interval_s,
        needs_render=monitor.needs_render,
        selector_config=monitor.selector_config,
        rule_src=monitor.rule_src,
        version=monitor.version,
        created_at=monitor.created_at,
        tags=[t.name for t in monitor.tags],
    )


@router.delete("/monitors/{monitor_id}")
async def delete_monitor(
    monitor_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> dict[str, str]:
    """Soft-delete monitor, keeping historical audit log and snapshots intact."""
    if not if_match:
        raise APIError(
            status_code=400,
            title="Bad Request",
            detail="If-Match header is required for deletion",
        )

    stmt = select(Monitor).where(Monitor.id == monitor_id, Monitor.user_id == user.id)
    res = await session.execute(stmt)
    monitor = res.scalar_one_or_none()

    if not monitor:
        raise APIError(status_code=404, title="Not Found", detail="Monitor not found")

    clean_version = if_match.strip('"')
    if str(monitor.version) != clean_version:
        raise APIError(
            status_code=412,
            title="Precondition Failed",
            detail=f"Version mismatch: current monitor version is {monitor.version}",
        )

    # SOFT DELETE: Sets state='deleted', preserving historical snapshots and audit logs.
    monitor.state = "deleted"
    monitor.version += 1
    await session.flush()

    await log_audit_event(
        session,
        action="monitor_deleted",
        user_id=user.id,
        resource_type="monitor",
        resource_id=str(monitor.id),
        request=request,
    )
    await session.commit()
    return {"message": "Monitor deleted"}


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[TagResponse]:
    """List all tags created by the authenticated user."""
    stmt = select(Tag).where(Tag.user_id == user.id).order_by(Tag.name.asc())
    res = await session.execute(stmt)
    tags = list(res.scalars().all())
    return [TagResponse.model_validate(t) for t in tags]


@router.post("/tags", response_model=TagResponse, status_code=201)
async def create_tag(
    body: CreateTagRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TagResponse:
    """Create a user tag, returning the existing tag if already created."""
    tag = await _get_or_create_tag(session, user.id, body.name)
    await session.commit()
    return TagResponse.model_validate(tag)


@router.post("/monitors/{monitor_id}/tags", response_model=MonitorResponse)
async def add_monitor_tag(
    monitor_id: uuid.UUID,
    body: CreateTagRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MonitorResponse:
    """Associate a tag with a monitor."""
    stmt = (
        select(Monitor)
        .options(selectinload(Monitor.tags))
        .where(Monitor.id == monitor_id, Monitor.user_id == user.id)
    )
    res = await session.execute(stmt)
    monitor = res.scalar_one_or_none()
    if not monitor:
        raise APIError(status_code=404, title="Not Found", detail="Monitor not found")

    tag = await _get_or_create_tag(session, user.id, body.name)
    if tag not in monitor.tags:
        monitor.tags.append(tag)
        monitor.version += 1
        await session.flush()
        await session.commit()

    return MonitorResponse(
        id=monitor.id,
        url=monitor.url,
        url_canonical=monitor.url_canonical,
        domain=monitor.domain,
        state=monitor.state,
        min_interval_s=monitor.min_interval_s,
        max_interval_s=monitor.max_interval_s,
        needs_render=monitor.needs_render,
        selector_config=monitor.selector_config,
        rule_src=monitor.rule_src,
        version=monitor.version,
        created_at=monitor.created_at,
        tags=[t.name for t in monitor.tags],
    )


@router.delete("/monitors/{monitor_id}/tags/{tag_id}", response_model=MonitorResponse)
async def remove_monitor_tag(
    monitor_id: uuid.UUID,
    tag_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MonitorResponse:
    """Remove a tag association from a monitor."""
    stmt = (
        select(Monitor)
        .options(selectinload(Monitor.tags))
        .where(Monitor.id == monitor_id, Monitor.user_id == user.id)
    )
    res = await session.execute(stmt)
    monitor = res.scalar_one_or_none()
    if not monitor:
        raise APIError(status_code=404, title="Not Found", detail="Monitor not found")

    monitor.tags = [t for t in monitor.tags if t.id != tag_id]
    monitor.version += 1
    await session.flush()
    await session.commit()

    return MonitorResponse(
        id=monitor.id,
        url=monitor.url,
        url_canonical=monitor.url_canonical,
        domain=monitor.domain,
        state=monitor.state,
        min_interval_s=monitor.min_interval_s,
        max_interval_s=monitor.max_interval_s,
        needs_render=monitor.needs_render,
        selector_config=monitor.selector_config,
        rule_src=monitor.rule_src,
        version=monitor.version,
        created_at=monitor.created_at,
        tags=[t.name for t in monitor.tags],
    )
