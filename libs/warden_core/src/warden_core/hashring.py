"""Virtual-node hashing with caller-reported weighted bounded assignment loads."""

import bisect
import hashlib
import math


class ConsistentHashRing:
    """Deterministic clockwise ownership, optionally probing under-capacity members.

    report_load is explicit: lookups do not mutate assignments. Callers must
    release loads when work finishes. Caps use projected total load (including
    the incoming assignment), normalized capacity weights and ceil for indivisible
    jobs. This is a local estimate, not globally synchronized admission control.
    Setting epsilon=inf gives ordinary hashing for independent remap experiments.
    """

    def __init__(self, virtual_nodes_per_member: int, load_epsilon: float = 0.25) -> None:
        if virtual_nodes_per_member < 1 or math.isnan(load_epsilon) or load_epsilon < 0:
            raise ValueError("positive vnode count and nonnegative epsilon required")
        self._vnodes = virtual_nodes_per_member
        self._epsilon = load_epsilon
        self._ring: list[tuple[int, str]] = []
        self._weights: dict[str, float] = {}
        self._loads: dict[str, int] = {}

    @staticmethod
    def _hash(value: str) -> int:
        return int.from_bytes(hashlib.blake2b(value.encode(), digest_size=8).digest(), "big")

    def add_member(self, member_id: str, capacity_weight: float = 1.0) -> None:
        if not member_id or not math.isfinite(capacity_weight) or capacity_weight <= 0:
            raise ValueError("nonempty member and finite positive capacity required")
        if member_id in self._weights:
            if self._weights[member_id] != capacity_weight:
                raise ValueError("remove member before changing its capacity")
            return
        self._weights[member_id] = capacity_weight
        self._loads[member_id] = 0
        self._ring.extend((self._hash(f"{member_id}:{i}"), member_id) for i in range(self._vnodes))
        self._ring.sort()

    def remove_member(self, member_id: str) -> None:
        self._weights.pop(member_id, None)
        self._loads.pop(member_id, None)
        self._ring = [entry for entry in self._ring if entry[1] != member_id]

    def get_member(self, key: str) -> str:
        if not self._ring:
            raise LookupError("no live members")
        index = bisect.bisect_left(self._ring, (self._hash(key), "")) % len(self._ring)
        if math.isinf(self._epsilon):
            return self._ring[index][1]
        total = sum(self._loads.values()) + 1
        total_weight = sum(self._weights.values())
        seen: set[str] = set()
        for offset in range(len(self._ring)):
            member = self._ring[(index + offset) % len(self._ring)][1]
            if member in seen:
                continue
            seen.add(member)
            cap = math.ceil((1 + self._epsilon) * total * self._weights[member] / total_weight)
            if self._loads[member] < cap:
                return member
        raise RuntimeError("all members exceed their projected load capacity")

    def report_load(self, member_id: str, delta: int) -> None:
        if member_id not in self._loads:
            raise KeyError(member_id)
        if self._loads[member_id] + delta < 0:
            raise ValueError("load cannot become negative")
        self._loads[member_id] += delta
