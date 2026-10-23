"""Packed Bloom membership and byte-counter membership, with stable double hashing."""

import hashlib
import math
from collections import Counter


class BloomFilter:
    """No false negatives for added bytes; positives require exact confirmation.

    The bytearray packs eight bits per byte. Double hashing derives all positions
    from one 128-bit BLAKE2 digest. Capacity overruns increase false positives;
    they do not erase membership. Size uses ceil(m), with at least one hash.
    """

    def __init__(self, expected_items: int, false_positive_rate: float) -> None:
        if expected_items < 1 or not 0 < false_positive_rate < 1:
            raise ValueError("expected_items must be positive and probability strictly in (0, 1)")
        self.m = max(
            8, math.ceil(-expected_items * math.log(false_positive_rate) / math.log(2) ** 2)
        )
        self.k = max(1, round(self.m / expected_items * math.log(2)))
        self._bits = bytearray((self.m + 7) // 8)

    def _positions(self, item: bytes) -> list[int]:
        digest = hashlib.blake2b(item, digest_size=16).digest()
        first = int.from_bytes(digest[:8], "big")
        second = int.from_bytes(digest[8:], "big") | 1
        return [(first + i * second) % self.m for i in range(self.k)]

    def add(self, item: bytes) -> None:
        for pos in self._positions(item):
            self._bits[pos // 8] |= 1 << (pos % 8)

    def __contains__(self, item: bytes) -> bool:
        return all(self._bits[pos // 8] & (1 << (pos % 8)) for pos in self._positions(item))


class CountingBloomFilter(BloomFilter):
    """Byte counters cost eight times the packed bits, permitting safe deletions.

    remove requires an outstanding add of that item. Membership cannot establish
    this precondition because positives can be false. Overflow rejects the whole
    add before mutation, rather than saturating and later creating false negatives.
    Duplicate hash positions count their full multiplicity.
    """

    def __init__(self, expected_items: int, false_positive_rate: float) -> None:
        super().__init__(expected_items, false_positive_rate)
        self._bits = bytearray()
        self._counts = bytearray(self.m)

    def add(self, item: bytes) -> None:
        positions = Counter(self._positions(item))
        if any(self._counts[pos] + n > 255 for pos, n in positions.items()):
            raise OverflowError("Bloom counter capacity exceeded; add was not applied")
        for pos, n in positions.items():
            self._counts[pos] += n

    def remove(self, item: bytes) -> None:
        positions = Counter(self._positions(item))
        if any(self._counts[pos] < n for pos, n in positions.items()):
            raise ValueError("remove requires a matching outstanding add")
        for pos, n in positions.items():
            self._counts[pos] -= n

    def __contains__(self, item: bytes) -> bool:
        return all(self._counts[pos] > 0 for pos in self._positions(item))
