"""Hierarchical wheel with tick-rounded deadlines and O(1) handle cancellation."""

from dataclasses import dataclass, field


@dataclass(eq=False, slots=True)
class TimerHandle:
    """Opaque ownership/location token; do not mutate its bookkeeping fields."""

    callback_id: str
    deadline_tick: int
    identity: int
    owner: object = field(repr=False)
    level: int = -1
    slot: int = 0
    active: bool = True


class TimingWheel:
    """Time starts at zero; caller chooses its epoch before loading timers.

    Positive delays round UP to the next tick, never firing early. Delay zero is
    ready on the next advance, even at the same timestamp. Equal-deadline callback
    order is unspecified. Coarser levels grow for deadlines beyond initial range.
    Runtime is proportional to elapsed ticks plus cascaded/fired entries, not all
    scheduled timers. Clocks must never move backwards.
    """

    def __init__(self, tick_ms: int, wheel_sizes: list[int]) -> None:
        if tick_ms < 1 or not wheel_sizes or any(size < 2 for size in wheel_sizes):
            raise ValueError("positive tick and wheel sizes >= 2 required")
        self.tick_ms = tick_ms
        self._sizes = list(wheel_sizes)
        self._spans = [1]
        for size in wheel_sizes[:-1]:
            self._spans.append(self._spans[-1] * size)
        self._wheels: list[list[dict[int, TimerHandle]]] = [
            [{} for _ in range(size)] for size in wheel_sizes
        ]
        self._ready: dict[int, TimerHandle] = {}
        self._tick = 0
        self._now_ms = 0
        self._sequence = 0

    def schedule(self, delay_ms: int, callback_id: str) -> TimerHandle:
        if delay_ms < 0:
            raise ValueError("negative delay")
        self._sequence += 1
        deadline = (self._now_ms + delay_ms + self.tick_ms - 1) // self.tick_ms
        handle = TimerHandle(callback_id, deadline, self._sequence, self)
        if delay_ms == 0:
            self._ready[handle.identity] = handle
        else:
            self._place(handle)
        return handle

    def _place(self, handle: TimerHandle) -> None:
        delta = max(0, handle.deadline_tick - self._tick)
        while delta >= self._spans[-1] * self._sizes[-1]:
            self._spans.append(self._spans[-1] * self._sizes[-1])
            self._sizes.append(self._sizes[-1])
            self._wheels.append([{} for _ in range(self._sizes[-1])])
        level = next(
            index for index, span in enumerate(self._spans) if delta < span * self._sizes[index]
        )
        slot = handle.deadline_tick // self._spans[level] % self._sizes[level]
        handle.level, handle.slot = level, slot
        self._wheels[level][slot][handle.identity] = handle

    def cancel(self, handle: TimerHandle) -> bool:
        if handle.owner is not self or not handle.active:
            return False
        bucket = self._ready if handle.level == -1 else self._wheels[handle.level][handle.slot]
        del bucket[handle.identity]
        handle.active = False
        return True

    def advance(self, now_ms: int) -> list[str]:
        if now_ms < self._now_ms:
            raise ValueError("clock moved backwards")
        fired: list[str] = []
        for handle in self._ready.values():
            handle.active = False
            fired.append(handle.callback_id)
        self._ready.clear()
        target = now_ms // self.tick_ms
        while self._tick < target:
            self._tick += 1
            for level in range(len(self._wheels) - 1, 0, -1):
                span = self._spans[level]
                if self._tick % span == 0:
                    slot = self._tick // span % self._sizes[level]
                    entries = self._wheels[level][slot]
                    self._wheels[level][slot] = {}
                    for handle in entries.values():
                        self._place(handle)
            slot = self._tick % self._sizes[0]
            entries = self._wheels[0][slot]
            self._wheels[0][slot] = {}
            for handle in entries.values():
                if handle.deadline_tick <= self._tick:
                    handle.active = False
                    fired.append(handle.callback_id)
                else:
                    self._place(handle)
        self._now_ms = now_ms
        return fired
