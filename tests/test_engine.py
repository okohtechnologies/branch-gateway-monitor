import pytest

from ipmon.engine import (
    BranchConfig,
    Debouncer,
    Engine,
    MonitorSuspectEnded,
    MonitorSuspectStarted,
    ProbeResult,
    Status,
    Transition,
    classify,
)

GOOD = ProbeResult(avg_latency_ms=20.0, loss_pct=0.0)
SLOW = ProbeResult(avg_latency_ms=400.0, loss_pct=0.0)
LOSSY = ProbeResult(avg_latency_ms=30.0, loss_pct=50.0)
DEAD = ProbeResult(avg_latency_ms=None, loss_pct=100.0)


# --- classify (FR-10) -------------------------------------------------------


def test_classify_up():
    assert classify(GOOD, 200, 20) == Status.UP


def test_classify_down_on_total_loss():
    assert classify(DEAD, 200, 20) == Status.DOWN


def test_classify_degraded_on_latency():
    assert classify(SLOW, 200, 20) == Status.DEGRADED


def test_classify_degraded_on_loss():
    assert classify(LOSSY, 200, 20) == Status.DEGRADED


def test_classify_at_threshold_is_not_degraded():
    r = ProbeResult(avg_latency_ms=200.0, loss_pct=20.0)
    assert classify(r, 200, 20) == Status.UP


# --- Debouncer (FR-11) ------------------------------------------------------


def test_down_needs_three_consecutive():
    d = Debouncer(3)
    d.update(Status.UP)
    d.update(Status.UP)
    d.update(Status.UP)  # baseline UP, no event
    assert d.update(Status.DOWN) is None
    assert d.update(Status.DOWN) is None
    assert d.update(Status.DOWN) == (Status.UP, Status.DOWN)


def test_first_up_confirmation_is_silent():
    d = Debouncer(3)
    assert [d.update(Status.UP) for _ in range(3)] == [None, None, None]
    assert d.confirmed == Status.UP


def test_first_confirmation_down_is_reported():
    d = Debouncer(3)
    d.update(Status.DOWN)
    d.update(Status.DOWN)
    assert d.update(Status.DOWN) == (None, Status.DOWN)


def test_flapping_never_confirms():
    d = Debouncer(3)
    for _ in range(3):
        d.update(Status.UP)
    for _ in range(10):
        assert d.update(Status.DOWN) is None
        assert d.update(Status.UP) is None
    assert d.confirmed == Status.UP


def test_interrupted_streak_resets():
    d = Debouncer(3)
    for _ in range(3):
        d.update(Status.UP)
    d.update(Status.DOWN)
    d.update(Status.DOWN)
    d.update(Status.UP)  # breaks the streak
    assert d.update(Status.DOWN) is None
    assert d.update(Status.DOWN) is None
    assert d.update(Status.DOWN) == (Status.UP, Status.DOWN)


def test_candidate_switch_restarts_count():
    d = Debouncer(3)
    for _ in range(3):
        d.update(Status.UP)
    d.update(Status.DOWN)
    d.update(Status.DOWN)
    d.update(Status.DEGRADED)  # different candidate, count restarts at 1
    assert d.update(Status.DEGRADED) is None
    assert d.update(Status.DEGRADED) == (Status.UP, Status.DEGRADED)


def test_recovery_needs_three_good():
    d = Debouncer(3)
    for _ in range(3):
        d.update(Status.DOWN)
    assert d.update(Status.UP) is None
    assert d.update(Status.UP) is None
    assert d.update(Status.UP) == (Status.DOWN, Status.UP)


def test_confirm_one_is_immediate():
    d = Debouncer(1)
    d.update(Status.UP)
    assert d.update(Status.DOWN) == (Status.UP, Status.DOWN)


# --- Engine: single branch --------------------------------------------------

A = BranchConfig(1, latency_warn_ms=200, loss_warn_pct=20)
B = BranchConfig(2, latency_warn_ms=200, loss_warn_pct=20)


def cycle(engine, branches, per_branch, canary=GOOD):
    return engine.process_cycle(branches, {b.id: r for b, r in zip(branches, per_branch)}, canary)


def warm_up(engine, branches, n=3):
    for _ in range(n):
        cycle(engine, branches, [GOOD] * len(branches))


def test_outage_alerts_on_third_failure_and_recovers():
    e = Engine(confirm=3)
    warm_up(e, [A])
    assert cycle(e, [A], [DEAD]) == []
    assert cycle(e, [A], [DEAD]) == []
    events = cycle(e, [A], [DEAD])
    assert events == [Transition(1, Status.UP, Status.DOWN, DEAD)]
    assert cycle(e, [A], [GOOD]) == []
    assert cycle(e, [A], [GOOD]) == []
    assert cycle(e, [A], [GOOD]) == [Transition(1, Status.DOWN, Status.UP, GOOD)]


def test_down_to_degraded_is_a_transition():
    e = Engine(confirm=3)
    warm_up(e, [A])
    for _ in range(3):
        cycle(e, [A], [DEAD])
    for _ in range(2):
        assert cycle(e, [A], [SLOW]) == []
    assert cycle(e, [A], [SLOW]) == [Transition(1, Status.DOWN, Status.DEGRADED, SLOW)]


def test_removed_branch_is_forgotten():
    e = Engine(confirm=3)
    warm_up(e, [A, B])
    cycle(e, [A], [GOOD])
    assert e.confirmed(2) is None


# --- Engine: monitor suspect (FR-33, FR-34) ---------------------------------


def test_all_down_with_canary_down_is_one_suspect_alert():
    e = Engine(confirm=3)
    warm_up(e, [A, B])
    cycle(e, [A, B], [DEAD, DEAD], canary=DEAD)
    cycle(e, [A, B], [DEAD, DEAD], canary=DEAD)
    events = cycle(e, [A, B], [DEAD, DEAD], canary=DEAD)
    assert events == [MonitorSuspectStarted()]
    assert e.monitor_suspect


def test_all_down_with_canary_up_alerts_per_branch():
    """Canary fine but every branch dead: likely the VPN, not the monitor."""
    e = Engine(confirm=3)
    warm_up(e, [A, B])
    for _ in range(2):
        cycle(e, [A, B], [DEAD, DEAD], canary=GOOD)
    events = cycle(e, [A, B], [DEAD, DEAD], canary=GOOD)
    assert events == [
        Transition(1, Status.UP, Status.DOWN, DEAD),
        Transition(2, Status.UP, Status.DOWN, DEAD),
    ]
    assert not e.monitor_suspect


def test_canary_down_but_one_branch_up_is_not_suspect():
    e = Engine(confirm=3)
    warm_up(e, [A, B])
    for _ in range(2):
        cycle(e, [A, B], [DEAD, GOOD], canary=DEAD)
    events = cycle(e, [A, B], [DEAD, GOOD], canary=DEAD)
    assert events == [Transition(1, Status.UP, Status.DOWN, DEAD)]


def test_single_branch_is_never_suspect():
    e = Engine(confirm=3)
    warm_up(e, [A])
    for _ in range(2):
        cycle(e, [A], [DEAD], canary=DEAD)
    events = cycle(e, [A], [DEAD], canary=DEAD)
    assert events == [Transition(1, Status.UP, Status.DOWN, DEAD)]


def test_recovery_during_suspect_is_silent_and_ends_suspicion():
    e = Engine(confirm=3)
    warm_up(e, [A, B])
    for _ in range(3):
        cycle(e, [A, B], [DEAD, DEAD], canary=DEAD)
    # Network returns: everything recovers over three cycles.
    cycle(e, [A, B], [GOOD, GOOD], canary=GOOD)
    cycle(e, [A, B], [GOOD, GOOD], canary=GOOD)
    events = cycle(e, [A, B], [GOOD, GOOD], canary=GOOD)
    assert events == [MonitorSuspectEnded()]
    assert not e.monitor_suspect


def test_branch_still_down_is_alerted_when_suspicion_clears():
    e = Engine(confirm=3)
    warm_up(e, [A, B])
    for _ in range(3):
        cycle(e, [A, B], [DEAD, DEAD], canary=DEAD)
    # Canary and B recover, A stays dead.
    cycle(e, [A, B], [DEAD, GOOD], canary=GOOD)
    cycle(e, [A, B], [DEAD, GOOD], canary=GOOD)
    events = cycle(e, [A, B], [DEAD, GOOD], canary=GOOD)
    assert MonitorSuspectEnded() in events
    assert Transition(1, Status.UP, Status.DOWN, DEAD) in events
    assert not any(isinstance(x, Transition) and x.branch_id == 2 for x in events)


def test_no_canary_configured_never_suspect():
    e = Engine(confirm=3)
    warm_up(e, [A, B])
    for _ in range(2):
        cycle(e, [A, B], [DEAD, DEAD], canary=None)
    events = cycle(e, [A, B], [DEAD, DEAD], canary=None)
    assert len(events) == 2 and not e.monitor_suspect


# --- Edge cases --------------------------------------------------------------


def test_confirm_below_one_rejected():
    import pytest

    with pytest.raises(ValueError):
        Debouncer(0)


def test_branch_without_result_is_skipped():
    e = Engine(confirm=3)
    events = e.process_cycle([A, B], {1: GOOD}, GOOD)  # no result for branch 2
    assert events == [] and e.confirmed(2) is None


def test_degraded_after_suppressed_down_reports_original_old_status():
    """A DOWN swallowed by suspicion later turns DEGRADED once the canary is back."""
    e = Engine(confirm=3)
    warm_up(e, [A, B])
    for _ in range(3):
        cycle(e, [A, B], [DEAD, DEAD], canary=DEAD)
    cycle(e, [A, B], [SLOW, GOOD], canary=GOOD)
    cycle(e, [A, B], [SLOW, GOOD], canary=GOOD)
    events = cycle(e, [A, B], [SLOW, GOOD], canary=GOOD)
    assert MonitorSuspectEnded() in events
    assert Transition(1, Status.UP, Status.DEGRADED, SLOW) in events


# --- settings changed while running -------------------------------------------------------------


def test_set_confirm_changes_how_fast_an_outage_is_confirmed():
    e = Engine(confirm=3)
    warm_up(e, [A])
    e.set_confirm(2)
    assert cycle(e, [A], [DEAD]) == []
    assert cycle(e, [A], [DEAD]) == [Transition(1, Status.UP, Status.DOWN, DEAD)]  # two results now suffice


def test_set_confirm_rejects_nonsense():
    with pytest.raises(ValueError):
        Engine(confirm=3).set_confirm(0)


def test_reset_canary_stops_a_stale_canary_verdict_suppressing_alerts():
    e = Engine(confirm=3)
    warm_up(e, [A, B])
    for _ in range(3):
        cycle(e, [A, B], [DEAD, DEAD], canary=DEAD)
    assert e.monitor_suspect  # the canary is confirmed down and everything fails
    e.reset_canary()  # the canary was switched off or retargeted
    # one more cycle without a canary: nothing may keep the monitor in the suspect state
    events = cycle(e, [A, B], [DEAD, DEAD], canary=None)
    assert not e.monitor_suspect
    assert MonitorSuspectEnded() in events
