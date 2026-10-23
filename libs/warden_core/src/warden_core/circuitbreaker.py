"""Per-domain rolling circuit breaker with one exclusive half-open probe."""

from collections import deque
from dataclasses import dataclass, field


@dataclass
class _Domain:
    outcomes: deque[tuple[float, bool]] = field(default_factory=deque)
    opened_until: float = 0
    probe: bool = False


class CircuitBreaker:
    """Local to one fetcher; no cross-replica consistency claim."""

    def __init__(
        self,
        *,
        window_s: float = 60,
        cooldown_s: float = 15,
        minimum: int = 5,
        failure_ratio: float = 0.6,
    ) -> None:
        if window_s <= 0 or cooldown_s <= 0 or minimum < 1 or not 0 < failure_ratio <= 1:
            raise ValueError("invalid breaker limits")
        self.window_s, self.cooldown_s = window_s, cooldown_s
        self.minimum, self.failure_ratio = minimum, failure_ratio
        self._domains: dict[str, _Domain] = {}

    def allow(self, domain: str, now: float) -> bool:
        state = self._domains.setdefault(domain, _Domain())
        if state.opened_until:
            if now < state.opened_until or state.probe:
                return False
            state.probe = True
        return True

    def record(self, domain: str, success: bool, now: float) -> None:
        state = self._domains.setdefault(domain, _Domain())
        if state.probe:
            state.probe = False
            if success:
                state.outcomes.clear()
                state.opened_until = 0
            else:
                state.opened_until = now + self.cooldown_s
            return
        state.outcomes.append((now, success))
        while state.outcomes and state.outcomes[0][0] < now - self.window_s:
            state.outcomes.popleft()
        failures = sum(not outcome for _, outcome in state.outcomes)
        if (
            len(state.outcomes) >= self.minimum
            and failures / len(state.outcomes) >= self.failure_ratio
        ):
            state.opened_until = now + self.cooldown_s
