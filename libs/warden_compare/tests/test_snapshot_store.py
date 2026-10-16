"""Property-based and unit tests for snapshot_store encoding and reconstruction."""

import random

import hypothesis.strategies as st
from hypothesis import given, settings

from warden_compare.snapshot_store import (
    deserialize_edit_script,
    encode_version,
    reconstruct,
    serialize_edit_script,
)
from warden_compare.textdiff import Delete, EditScript, Insert, Keep, apply_edit_script


def _generate_random_mutation(lines: list[str]) -> tuple[list[str], EditScript]:
    """Apply a random set of edit ops to lines, returning (new_lines, script)."""
    script: EditScript = []
    new_lines: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        action = random.choice(["keep", "delete", "replace", "keep"])
        if action == "keep":
            keep_len = min(random.randint(1, 3), n - i)
            script.append(Keep(keep_len))
            new_lines.extend(lines[i : i + keep_len])
            i += keep_len
        elif action == "delete":
            del_len = min(random.randint(1, 2), n - i)
            script.append(Delete(del_len))
            i += del_len
        else:  # replace
            del_len = min(1, n - i)
            ins_items = [f"mutated_token_{random.randint(1, 1000)}"]
            script.append(Delete(del_len))
            script.append(Insert(ins_items))
            new_lines.extend(ins_items)
            i += del_len

    # Optional trailing insertion
    if random.random() < 0.3:
        trail_items = [f"trailing_{random.randint(1, 1000)}"]
        script.append(Insert(trail_items))
        new_lines.extend(trail_items)

    return new_lines, script


def test_edit_script_serialization_roundtrip():
    """Verify EditScript JSON serialization and deserialization is losslessly reversible."""
    original_script: EditScript = [
        Keep(n=5),
        Delete(n=2),
        Insert(items=["line A", "line B"]),
        Keep(n=1),
    ]
    serialized = serialize_edit_script(original_script)
    restored = deserialize_edit_script(serialized)
    assert restored == original_script


def test_encode_version_triggers():
    """Verify independent triggers for keyframe vs delta storage."""
    raw = b"<html><body>" + b"<p>Content paragraph line</p>\n" * 50 + b"</body></html>"

    # 1. Bootstrap: no prior keyframe -> keyframe
    v1 = encode_version(None, None, raw, keyframe_interval=20, versions_since_keyframe=0)
    assert v1.is_keyframe is True

    # 2. Keyframe interval trigger: versions_since_keyframe >= interval -> keyframe
    small_script: EditScript = [Keep(2)]
    v_interval = encode_version(
        v1.compressed_data,
        small_script,
        raw,
        keyframe_interval=5,
        versions_since_keyframe=5,
    )
    assert v_interval.is_keyframe is True

    # 3. Small delta under 50% threshold -> delta
    v_delta = encode_version(
        v1.compressed_data,
        small_script,
        raw,
        keyframe_interval=20,
        versions_since_keyframe=1,
    )
    assert v_delta.is_keyframe is False

    # 4. Giant delta exceeding 50% threshold -> keyframe
    giant_script: EditScript = [Insert([f"giant line {i}" for i in range(500)])]
    v_large = encode_version(
        v1.compressed_data,
        giant_script,
        raw,
        keyframe_interval=20,
        versions_since_keyframe=1,
    )
    assert v_large.is_keyframe is True


@settings(max_examples=10, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=1_000_000),
    chain_length=st.integers(min_value=50, max_value=80),
)
def test_hypothesis_mutation_chains_reconstruct_exact_content_at_every_version(
    seed: int, chain_length: int
):
    """Property test: random mutation chains of 50-80 versions reconstruct exactly at every step.

    INVARIANT TESTED:
    For every version v in 0..chain_length:
    reconstruct(active_keyframe, [delta_1, ..., delta_k]) == original_content[v]
    """
    random.seed(seed)
    current_lines = [f"initial line {i}" for i in range(10)]
    current_bytes = "\n".join(current_lines).encode("utf-8")

    # Storage structures simulating database snapshot chain
    active_keyframe: bytes = b""
    active_deltas: list[bytes] = []
    versions_since_kf = 0
    keyframe_interval = 15

    # Bootstrap version 0
    v0 = encode_version(
        None,
        None,
        current_bytes,
        keyframe_interval=keyframe_interval,
        versions_since_keyframe=0,
    )
    assert v0.is_keyframe is True
    active_keyframe = v0.compressed_data
    active_deltas = []

    # Verify version 0 reconstruction
    reconstructed_0 = reconstruct(active_keyframe, active_deltas)
    assert reconstructed_0 == current_bytes

    # Simulate sequential version mutations
    for v_idx in range(1, chain_length):
        new_lines, script = _generate_random_mutation(current_lines)
        # Ensure apply_edit_script matches generated new lines
        assert apply_edit_script(current_lines, script) == new_lines

        new_bytes = "\n".join(new_lines).encode("utf-8")
        versions_since_kf += 1

        encoded = encode_version(
            active_keyframe,
            script,
            new_bytes,
            keyframe_interval=keyframe_interval,
            versions_since_keyframe=versions_since_kf,
        )

        if encoded.is_keyframe:
            active_keyframe = encoded.compressed_data
            active_deltas = []
            versions_since_kf = 0
        else:
            active_deltas.append(encoded.compressed_data)

        # RECONSTRUCTION ASSERTION: MUST EQUAL AT EVERY SINGLE VERSION
        reconstructed = reconstruct(active_keyframe, active_deltas)
        assert reconstructed == new_bytes, f"Reconstruction failed at version index {v_idx}"

        current_lines = new_lines
        current_bytes = new_bytes
