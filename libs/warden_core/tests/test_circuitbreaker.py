from warden_core.circuitbreaker import CircuitBreaker


def test_closed_open_exclusive_probe_recovery_and_domain_isolation() -> None:
    breaker = CircuitBreaker(minimum=3, cooldown_s=10)
    for now in range(3):
        assert breaker.allow("bad.example", now)
        breaker.record("bad.example", False, now)
    assert not breaker.allow("bad.example", 3)
    assert breaker.allow("healthy.example", 3)
    assert breaker.allow("bad.example", 13)
    assert not breaker.allow("bad.example", 13)
    breaker.record("bad.example", True, 13)
    assert breaker.allow("bad.example", 14)


def test_failed_probe_reopens_and_old_window_is_not_counted() -> None:
    breaker = CircuitBreaker(minimum=2, window_s=5, cooldown_s=2)
    breaker.record("example", False, 0)
    breaker.record("example", True, 10)
    assert breaker.allow("example", 10)
    breaker.record("example", False, 11)
    breaker.record("example", False, 12)
    assert not breaker.allow("example", 13)
    assert breaker.allow("example", 15)
    breaker.record("example", False, 15)
    assert not breaker.allow("example", 16)
