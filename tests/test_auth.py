import time

from ipmon.auth import LoginThrottle


def test_retry_after_zero_until_blocked_then_counts_down(monkeypatch):
    t = LoginThrottle(limit=3, window=100.0)
    clock = {"now": 1000.0}
    monkeypatch.setattr("ipmon.auth.time.monotonic", lambda: clock["now"])
    assert t.retry_after("ip") == 0
    for _ in range(2):
        t.record_failure("ip")
    assert t.retry_after("ip") == 0 and not t.blocked("ip")
    t.record_failure("ip")
    assert t.blocked("ip")
    assert 99 <= t.retry_after("ip") <= 101
    clock["now"] += 60
    assert 39 <= t.retry_after("ip") <= 41
    clock["now"] += 41
    assert t.retry_after("ip") == 0 and not t.blocked("ip")


def test_retry_after_is_per_client():
    t = LoginThrottle(limit=1, window=50.0)
    t.record_failure("a")
    assert t.retry_after("a") > 0 and t.retry_after("b") == 0
