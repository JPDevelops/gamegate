"""The routing engine: pure business logic, no I/O, no framework.

Given the user's availability state and an event's priority, decide what
happens right now. This is deliberately a plain function so it can be tested
with plain Python values.
"""
from enum import Enum

from app.models.event import EventPriority
from app.models.status import AvailabilityState


class Decision(str, Enum):
    DELIVER_NOW = "deliver_now"
    QUEUE = "queue"
    SUPPRESS = "suppress"


def decide(
    state: AvailabilityState,
    priority: EventPriority,
    urgent_breaks_through_gaming: bool = True,
) -> Decision:
    if priority == EventPriority.IGNORE:
        return Decision.SUPPRESS

    if state == AvailabilityState.AVAILABLE:
        return Decision.DELIVER_NOW

    if state == AvailabilityState.FOCUSED:
        if priority == EventPriority.URGENT:
            return Decision.DELIVER_NOW
        return Decision.QUEUE

    if state == AvailabilityState.GAMING:
        if priority == EventPriority.URGENT and urgent_breaks_through_gaming:
            return Decision.DELIVER_NOW
        return Decision.QUEUE

    # AWAY: nothing interrupts; everything waits for the digest.
    return Decision.QUEUE
