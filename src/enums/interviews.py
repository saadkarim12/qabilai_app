"""Interview-scheduling enumerations (Step 6.4).

Backs the Calendly-driven interview booking flow that fires when HR moves an
application into the ``interview`` stage. One ``interview_bookings`` row tracks
a single application's invite → booking lifecycle; :class:`InterviewBookingState`
is that row's state-machine cursor.

Keep the existing values stable so historical rows continue to parse.
"""

from __future__ import annotations

from enum import StrEnum


class InterviewBookingState(StrEnum):
    """Lifecycle of one application's interview booking.

    * ``INVITED`` — a single-use Calendly link was minted and the booking
      email sent; we're waiting for the candidate to pick a slot. This is the
      initial state and the only one in which the reminder / timeout follow-ups
      act.
    * ``BOOKED`` — the candidate scheduled a slot (Calendly ``invitee.created``
      webhook). The scheduled time + Calendly event/invitee URIs are recorded.
      A reschedule stays ``BOOKED`` with an updated time.
    * ``CANCELED`` — the candidate (or HR, in Calendly) canceled the scheduled
      slot (``invitee.canceled``). The application stage is intentionally left
      untouched for HR to decide the next move.
    """

    INVITED = "invited"
    BOOKED = "booked"
    CANCELED = "canceled"
