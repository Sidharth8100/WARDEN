"""Security and audit logging helper."""

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from warden_db.models import AuditLog


async def log_audit_event(
    session: AsyncSession,
    *,
    action: str,
    request: Request,
    user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Write an audit trail row recording an action, extracting client IP and User-Agent.

    Extracts ip_address from x-forwarded-for or request.client, and user_agent
    from headers.
    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        ip_address = forwarded_for.split(",")[0].strip()
    elif request.client:
        ip_address = request.client.host
    else:
        ip_address = None

    user_agent = request.headers.get("user-agent")

    audit_entry = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.add(audit_entry)
    await session.flush()
    return audit_entry
