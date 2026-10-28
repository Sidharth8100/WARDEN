"""Object storage client for WARDEN using S3-compatible APIs (SeaweedFS).

Blobs are keyed by their SHA-256 digest with two-character prefix sharding:
`blobs/<hash[:2]>/<hash>`. Sharding prevents directories with millions of flat entries,
which degrade filesystem and index performance.
"""

import hashlib
from dataclasses import dataclass, field

import aioboto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]


class BlobNotFoundError(ValueError):
    """The store explicitly reports a missing object, not a network/auth failure."""


@dataclass(frozen=True)
class ObjectStoreConfig:
    """Configuration for connecting to an S3-compatible object store."""

    endpoint_url: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    bucket: str


def compute_blob_key(blob_hash: str) -> str:
    """Generate sharded S3 object key for a SHA-256 content hash."""
    prefix = blob_hash[:2]
    return f"blobs/{prefix}/{blob_hash}"


async def put_blob(
    client_config: ObjectStoreConfig,
    data: bytes,
    *,
    content_type: str = "application/octet-stream",
) -> str:
    """Store data in object storage keyed by its SHA-256 hash.

    Returns the hex-encoded SHA-256 hash reference.
    Overwriting an existing object with identical data is a safe, idempotent no-op.
    """
    blob_hash = hashlib.sha256(data).hexdigest()
    key = compute_blob_key(blob_hash)

    session = aioboto3.Session()
    s3_cfg = Config(s3={"addressing_style": "path"})
    async with session.client(
        "s3",
        endpoint_url=client_config.endpoint_url,
        aws_access_key_id=client_config.access_key,
        aws_secret_access_key=client_config.secret_key,
        config=s3_cfg,
    ) as s3:
        await s3.put_object(
            Bucket=client_config.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    return blob_hash


async def get_blob(
    client_config: ObjectStoreConfig, key_or_hash: str, *, max_bytes: int = 10_000_000
) -> bytes:
    """Retrieve raw bytes for an object key or hash."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    key = compute_blob_key(key_or_hash) if not key_or_hash.startswith("blobs/") else key_or_hash

    session = aioboto3.Session()
    s3_cfg = Config(s3={"addressing_style": "path"})
    async with session.client(
        "s3",
        endpoint_url=client_config.endpoint_url,
        aws_access_key_id=client_config.access_key,
        aws_secret_access_key=client_config.secret_key,
        config=s3_cfg,
    ) as s3:
        try:
            response = await s3.get_object(
                Bucket=client_config.bucket,
                Key=key,
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                raise BlobNotFoundError("snapshot_object_missing") from None
            raise
        body = response["Body"]
        chunks: list[bytes] = []
        total = 0
        async with body:
            while True:
                chunk: bytes = await body.read(min(65_536, max_bytes + 1 - total))
                if not chunk:
                    return b"".join(chunks)
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("object exceeds processing byte budget")
                chunks.append(chunk)


async def blob_exists(client_config: ObjectStoreConfig, key_or_hash: str) -> bool:
    """Check whether a blob exists in object storage without downloading its content."""
    key = compute_blob_key(key_or_hash) if not key_or_hash.startswith("blobs/") else key_or_hash

    session = aioboto3.Session()
    s3_cfg = Config(s3={"addressing_style": "path"})
    async with session.client(
        "s3",
        endpoint_url=client_config.endpoint_url,
        aws_access_key_id=client_config.access_key,
        aws_secret_access_key=client_config.secret_key,
        config=s3_cfg,
    ) as s3:
        try:
            await s3.head_object(
                Bucket=client_config.bucket,
                Key=key,
            )
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise


async def ensure_bucket_exists(client_config: ObjectStoreConfig) -> None:
    """Ensure the target bucket exists in object storage, creating it idempotently."""
    session = aioboto3.Session()
    s3_cfg = Config(s3={"addressing_style": "path"})
    async with session.client(
        "s3",
        endpoint_url=client_config.endpoint_url,
        aws_access_key_id=client_config.access_key,
        aws_secret_access_key=client_config.secret_key,
        config=s3_cfg,
    ) as s3:
        try:
            await s3.head_bucket(Bucket=client_config.bucket)
        except ClientError:
            try:
                await s3.create_bucket(Bucket=client_config.bucket)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    raise
