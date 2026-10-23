"""Unit tests for object storage client in libs/warden_net."""

import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from warden_net.objectstore import (
    ObjectStoreConfig,
    blob_exists,
    compute_blob_key,
    ensure_bucket_exists,
    get_blob,
    put_blob,
)


@pytest.fixture
def store_config() -> ObjectStoreConfig:
    return ObjectStoreConfig(
        endpoint_url="http://mock-s3:8333",
        access_key="test-key",
        secret_key="test-secret",  # noqa: S106
        bucket="warden-test",
    )


def test_compute_blob_key() -> None:
    data = b"Hello Warden Storage"
    h = hashlib.sha256(data).hexdigest()
    key = compute_blob_key(h)
    assert key == f"blobs/{h[:2]}/{h}"


@pytest.mark.asyncio
async def test_put_and_get_blob_mocked(store_config: ObjectStoreConfig) -> None:
    data = b"Snapshot content payload"
    expected_hash = hashlib.sha256(data).hexdigest()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    mock_body = AsyncMock()
    mock_body.read = AsyncMock(side_effect=[data[:4], data[4:], b""])
    mock_client.get_object.return_value = {"Body": mock_body}

    with patch("aioboto3.Session.client", return_value=mock_client):
        returned_hash = await put_blob(store_config, data)
        assert returned_hash == expected_hash
        mock_client.put_object.assert_called_once()

        fetched_data = await get_blob(store_config, returned_hash)
        assert fetched_data == data


@pytest.mark.asyncio
async def test_blob_exists_and_bucket_creation(store_config: ObjectStoreConfig) -> None:
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("aioboto3.Session.client", return_value=mock_client):
        exists = await blob_exists(store_config, "aabbcc")
        assert exists is True

        await ensure_bucket_exists(store_config)
        mock_client.head_bucket.assert_called_once()


@pytest.mark.asyncio
async def test_get_blob_rejects_oversized_chunked_body_and_closes_it(
    store_config: ObjectStoreConfig,
) -> None:
    client = AsyncMock()
    client.__aenter__.return_value = client
    body = AsyncMock()
    body.read.side_effect = [b"abc", b"def"]
    client.get_object.return_value = {"Body": body}
    with (
        patch("aioboto3.Session.client", return_value=client),
        pytest.raises(ValueError, match="byte budget"),
    ):
        await get_blob(store_config, "test", max_bytes=5)
    body.__aexit__.assert_awaited_once()
