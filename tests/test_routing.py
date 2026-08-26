"""The routing table, exhaustively. Pure logic — no HTTP, no database."""
import pytest

from app.models.event import EventPriority
from app.models.status import AvailabilityState
from app.services.routing import Decision, decide

A = AvailabilityState
P = EventPriority
D = Decision

EXPECTED = [
    (A.AVAILABLE, P.URGENT, D.DELIVER_NOW),
    (A.AVAILABLE, P.ACTIONABLE, D.DELIVER_NOW),
    (A.AVAILABLE, P.INFORMATIONAL, D.DELIVER_NOW),
    (A.AVAILABLE, P.IGNORE, D.SUPPRESS),
    (A.FOCUSED, P.URGENT, D.DELIVER_NOW),
    (A.FOCUSED, P.ACTIONABLE, D.QUEUE),
    (A.FOCUSED, P.INFORMATIONAL, D.QUEUE),
    (A.FOCUSED, P.IGNORE, D.SUPPRESS),
    (A.GAMING, P.URGENT, D.DELIVER_NOW),
    (A.GAMING, P.ACTIONABLE, D.QUEUE),
    (A.GAMING, P.INFORMATIONAL, D.QUEUE),
    (A.GAMING, P.IGNORE, D.SUPPRESS),
    (A.AWAY, P.URGENT, D.QUEUE),
    (A.AWAY, P.ACTIONABLE, D.QUEUE),
    (A.AWAY, P.INFORMATIONAL, D.QUEUE),
    (A.AWAY, P.IGNORE, D.SUPPRESS),
]


@pytest.mark.parametrize(("state", "priority", "expected"), EXPECTED)
def test_routing_table(state, priority, expected):
    assert decide(state, priority) == expected


def test_breakthrough_policy_off_queues_urgent_during_gaming():
    assert decide(A.GAMING, P.URGENT, urgent_breaks_through_gaming=False) == D.QUEUE
    # The policy only affects GAMING — focused urgent still delivers.
    assert decide(A.FOCUSED, P.URGENT, urgent_breaks_through_gaming=False) == D.DELIVER_NOW


def test_available_holds_non_urgent_by_default_pop_for_urgent():
    """The quiet default (ping_non_urgent=False): while free, only urgent pops —
    non-urgent waits in the inbox/recap. This is what stopped the Blink alerts."""
    assert decide(A.AVAILABLE, P.URGENT, ping_non_urgent=False) == D.DELIVER_NOW
    assert decide(A.AVAILABLE, P.ACTIONABLE, ping_non_urgent=False) == D.QUEUE
    assert decide(A.AVAILABLE, P.INFORMATIONAL, ping_non_urgent=False) == D.QUEUE
    # Opt non-urgent back in → everything pops when free.
    assert decide(A.AVAILABLE, P.INFORMATIONAL, ping_non_urgent=True) == D.DELIVER_NOW


def test_master_switch_no_popups_when_not_gaming():
    """notify_when_available=False → nothing pops while you're free, not even
    urgent; it all waits quietly in the inbox/recap."""
    assert decide(A.AVAILABLE, P.URGENT, notify_when_available=False) == D.QUEUE
    assert decide(A.AVAILABLE, P.INFORMATIONAL, notify_when_available=False) == D.QUEUE
