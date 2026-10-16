"""Single-process synchronous execution pipeline and Gate 1 verification."""

import contextlib
import hashlib
import time
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden_api.settings import get_settings
from warden_compare.classify import classify, extract_features, load_classifier_config
from warden_compare.normalize import normalize
from warden_compare.snapshot_store import encode_version
from warden_db.models import ChangeEvent, Monitor, Snapshot
from warden_fetch.orchestrate import MonitorFetchSpec, fetch
from warden_net.objectstore import ObjectStoreConfig, get_blob

_log = structlog.get_logger(__name__)


async def run_check(
    session: AsyncSession,
    monitor: Monitor,
) -> ChangeEvent | None:
    """Run an end-to-end synchronous monitoring check against a target URL.

    GATE 1 PIPELINE STAGES:
    1. Robots check and Tier-1 / Tier-2 fetch orchestration
    2. Write raw body and screenshot to object storage (claim-check pattern)
    3. Load previous latest snapshot (if any) and normalize both documents
    4. Structural Merkle tree diffing, SimHash, and feature extraction
    5. Weighted linear classification (meaningful change vs cosmetic noise)
    6. Atomically persist Snapshot and (if meaningful) ChangeEvent in single DB transaction.

    FIRST-CHECK BEHAVIOR:
    The first-ever check for a monitor unconditionally stores a baseline keyframe snapshot
    and returns None, because there is no prior historical snapshot to diff against.
    """
    settings = get_settings()
    store_config = ObjectStoreConfig(
        endpoint_url=settings.objectstore_endpoint,
        bucket=settings.objectstore_bucket,
        access_key=settings.objectstore_access_key.get_secret_value(),
        secret_key=settings.objectstore_secret_key.get_secret_value(),
    )

    # 1. Fetch orchestration
    spec = MonitorFetchSpec(
        url=monitor.url,
        needs_render=monitor.needs_render,
        selector_config=monitor.selector_config,
    )

    fetch_res = await fetch(
        spec,
        objectstore_config=store_config,
        extra_allowed_hosts=frozenset(settings.ssrf_test_allowlist),
        selenium_hub_url=settings.selenium_hub_url,
    )

    if fetch_res.blocked_by_robots:
        _log.info("pipeline.blocked_by_robots", monitor_id=str(monitor.id), url=monitor.url)
        return None

    if not fetch_res.content_hash:
        _log.warning("pipeline.empty_fetch", monitor_id=str(monitor.id), status=fetch_res.status)
        return None

    now_utc = datetime.now(UTC)
    now_bytes = await get_blob(store_config, fetch_res.content_hash)
    now_hash = hashlib.sha256(now_bytes).digest()

    # 2. Normalize current content
    t0 = time.monotonic()
    now_doc = normalize(now_bytes, selector_config=monitor.selector_config)
    elapsed_norm = (time.monotonic() - t0) * 1000.0
    _log.info("compare.normalize", elapsed_ms=round(elapsed_norm, 2), monitor_id=str(monitor.id))

    # 3. Query previous latest snapshot
    stmt = (
        select(Snapshot)
        .where(Snapshot.monitor_id == monitor.id)
        .order_by(Snapshot.fetched_at.desc())
        .limit(1)
    )
    prev_snapshot = (await session.execute(stmt)).scalars().first()

    # 4. First-ever check: bootstrap keyframe baseline
    if prev_snapshot is None:
        enc = encode_version(None, None, now_bytes)
        new_snapshot = Snapshot(
            fetched_at=now_utc,
            monitor_id=monitor.id,
            kind="keyframe",
            base_snapshot_id=None,
            content_hash=now_hash,
            blob_ref=fetch_res.content_hash,
            screenshot_ref=fetch_res.screenshot_hash,
            http_status=fetch_res.status,
            size_bytes=len(now_bytes),
            tier=fetch_res.tier_used,
        )
        session.add(new_snapshot)
        monitor.version += 1
        await session.commit()
        return None

    # 5. Successive check: compare against previous version
    prev_bytes = await get_blob(store_config, prev_snapshot.blob_ref or "")
    prev_doc = normalize(prev_bytes, selector_config=monitor.selector_config)

    prev_screenshot_bytes = None
    if prev_snapshot.screenshot_ref:
        with contextlib.suppress(Exception):
            prev_screenshot_bytes = await get_blob(store_config, prev_snapshot.screenshot_ref)

    now_screenshot_bytes = None
    if fetch_res.screenshot_hash:
        with contextlib.suppress(Exception):
            now_screenshot_bytes = await get_blob(store_config, fetch_res.screenshot_hash)

    t_diff_start = time.monotonic()
    features = extract_features(
        prev_doc,
        now_doc,
        old_screenshot=prev_screenshot_bytes,
        new_screenshot=now_screenshot_bytes,
    )
    elapsed_diff = (time.monotonic() - t_diff_start) * 1000.0
    _log.info("compare.diff", elapsed_ms=round(elapsed_diff, 2), monitor_id=str(monitor.id))

    # 6. Classify
    t_class_start = time.monotonic()
    weights, threshold = load_classifier_config()
    classification = classify(features, weights, threshold)
    elapsed_class = (time.monotonic() - t_class_start) * 1000.0
    _log.info(
        "compare.classify",
        score=classification.score,
        meaningful=classification.meaningful,
        elapsed_ms=round(elapsed_class, 2),
    )

    # 7. Snapshot storage encoding
    enc = encode_version(
        prev_bytes,
        edit_script=None,
        raw_content=now_bytes,
        keyframe_interval=settings.keyframe_interval,
        versions_since_keyframe=monitor.version % settings.keyframe_interval,
    )
    kind_val = "keyframe" if enc.is_keyframe else "delta"

    new_snapshot = Snapshot(
        fetched_at=now_utc,
        monitor_id=monitor.id,
        kind=kind_val,
        base_snapshot_id=prev_snapshot.id if not enc.is_keyframe else None,
        content_hash=now_hash,
        blob_ref=fetch_res.content_hash,
        screenshot_ref=fetch_res.screenshot_hash,
        http_status=fetch_res.status,
        size_bytes=len(now_bytes),
        tier=fetch_res.tier_used,
    )
    session.add(new_snapshot)
    await session.flush()

    change_event = None
    if classification.meaningful:
        change_event = ChangeEvent(
            monitor_id=monitor.id,
            from_snapshot_id=prev_snapshot.id,
            to_snapshot_id=new_snapshot.id,
            detected_at=now_utc,
            kind="structural",
            score=classification.score,
            meaningful=True,
            features=classification.features,
            classifier_version="v1",
        )
        session.add(change_event)

    monitor.version += 1
    await session.commit()

    if change_event is not None:
        await session.refresh(change_event)
        return change_event

    return None
