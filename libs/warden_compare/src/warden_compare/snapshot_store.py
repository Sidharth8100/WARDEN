"""Keyframe and delta snapshot encoding, zstandard compression, and chain reconstruction."""

import json
from dataclasses import dataclass

import zstandard

from warden_compare.textdiff import (
    Delete,
    EditScript,
    Insert,
    Keep,
    apply_edit_script,
)

# Compression level 3 provides an optimal trade-off: sub-millisecond compression
# times with 4-5x typical HTML compression ratio, avoiding the steep CPU curve of levels > 7.
DEFAULT_ZSTD_LEVEL = 3


@dataclass(frozen=True)
class EncodedVersion:
    """Outcome of keyframe-vs-delta snapshot encoding."""

    is_keyframe: bool
    compressed_data: bytes
    raw_size: int
    compressed_size: int


def serialize_edit_script(script: EditScript) -> bytes:
    """Serialize an EditScript into compact JSON UTF-8 bytes."""
    serialized = []
    for op in script:
        if isinstance(op, Keep):
            serialized.append({"op": "K", "n": op.n})
        elif isinstance(op, Delete):
            serialized.append({"op": "D", "n": op.n})
        elif isinstance(op, Insert):
            serialized.append({"op": "I", "items": op.items})
    return json.dumps(serialized, separators=(",", ":")).encode("utf-8")


def deserialize_edit_script(data: bytes) -> EditScript:
    """Deserialize compact JSON bytes back into an EditScript."""
    raw_list = json.loads(data.decode("utf-8"))
    script: EditScript = []
    for item in raw_list:
        op_type = item["op"]
        if op_type == "K":
            script.append(Keep(n=item["n"]))
        elif op_type == "D":
            script.append(Delete(n=item["n"]))
        elif op_type == "I":
            script.append(Insert(items=item["items"]))
        else:
            raise ValueError(f"Unknown operation type in serialized delta: {op_type}")
    return script


def encode_version(
    previous_keyframe: bytes | None,
    edit_script: EditScript | None,
    raw_content: bytes,
    *,
    keyframe_interval: int = 20,
    versions_since_keyframe: int = 0,
    compression_level: int = DEFAULT_ZSTD_LEVEL,
) -> EncodedVersion:
    """Encode a snapshot version as either a full keyframe or an incremental delta.

    STORAGE DECISION RULES:
    1. Every `keyframe_interval`-th version is unconditionally stored as a keyframe.
    2. Initial version (no previous keyframe or edit script) is stored as a keyframe.
    3. Incremental delta is stored UNLESS the compressed delta size exceeds 50% of the
       compressed keyframe size, in which case a fresh keyframe is stored instead.
    """
    cctx = zstandard.ZstdCompressor(level=compression_level)
    compressed_keyframe = cctx.compress(raw_content)

    # Condition 1: Mandatory interval keyframe or bootstrap keyframe
    if (
        previous_keyframe is None
        or edit_script is None
        or versions_since_keyframe >= keyframe_interval
    ):
        return EncodedVersion(
            is_keyframe=True,
            compressed_data=compressed_keyframe,
            raw_size=len(raw_content),
            compressed_size=len(compressed_keyframe),
        )

    # Condition 2: Evaluate delta size against 50% keyframe threshold
    delta_bytes = serialize_edit_script(edit_script)
    compressed_delta = cctx.compress(delta_bytes)

    if len(compressed_delta) > (len(compressed_keyframe) / 2.0):
        # Delta size exceeded half the keyframe size: promote to keyframe
        return EncodedVersion(
            is_keyframe=True,
            compressed_data=compressed_keyframe,
            raw_size=len(raw_content),
            compressed_size=len(compressed_keyframe),
        )

    return EncodedVersion(
        is_keyframe=False,
        compressed_data=compressed_delta,
        raw_size=len(raw_content),
        compressed_size=len(compressed_delta),
    )


def reconstruct(keyframe: bytes, deltas: list[bytes]) -> bytes:
    """Reconstruct raw content by decompressing the base keyframe and applying deltas in order."""
    dctx = zstandard.ZstdDecompressor()
    base_text = dctx.decompress(keyframe).decode("utf-8")
    lines = base_text.split("\n")

    for delta_comp in deltas:
        delta_bytes = dctx.decompress(delta_comp)
        script = deserialize_edit_script(delta_bytes)
        lines = apply_edit_script(lines, script)

    return "\n".join(lines).encode("utf-8")
