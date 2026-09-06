from django.conf import settings
from django.db import models


class MeetingVenue(models.Model):
    """
    A venue used specifically for meetings (Senate, Council, staff briefings,
    departmental meetings, workshops, etc.) — deliberately kept separate from
    room_management.Venue, which is reserved for lecture/exam timetabling.

    Managed entirely by the Utility Office from the Meeting Venues panel.
    """

    VENUE_TYPE_CHOICES = [
        ("hall", "Meeting Hall"),
        ("boardroom", "Boardroom"),
        ("conference", "Conference Room"),
        ("workshop", "Workshop"),
        ("lab", "Lab"),
        ("other", "Other"),
    ]

    code = models.CharField(max_length=50, unique=True)  # e.g. "BOARDROOM A", "SENATE HALL"
    name = models.CharField(max_length=150, blank=True, default="")
    location = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Free-text location, e.g. building/floor (e.g. 'Admin Block, 2nd Floor').",
    )
    capacity = models.PositiveIntegerField(null=True, blank=True)
    venue_type = models.CharField(max_length=20, choices=VENUE_TYPE_CHOICES, default="hall")
    is_workshop = models.BooleanField(
        default=False,
        help_text="Designates this meeting venue as a workshop facility.",
    )
    is_lab = models.BooleanField(
        default=False,
        help_text="Designates this meeting venue as a lab facility.",
    )
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to retire this venue from use without deleting its booking history.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Meeting Venue"
        verbose_name_plural = "Meeting Venues"

    def __str__(self):
        label = f"{self.code} - {self.name}" if self.name else self.code
        tags = []
        if self.is_workshop:
            tags.append("Workshop")
        if self.is_lab:
            tags.append("Lab")
        if tags:
            label += f" [{', '.join(tags)}]"
        return label

    @property
    def current_booking(self):
        """The live/active booking for this venue, if any."""
        return self.bookings.filter(is_current=True).first()

    @property
    def is_booked(self):
        return self.bookings.filter(is_current=True).exists()

    def conflicting_booking(self, *, date=None, day="", start_time=None, end_time=None,
                             exclude_booking_id=None):
        """
        Return the live booking that actually overlaps with the given
        date/day + start/end time window, or None if this venue is free
        for that slot.

        This replaces the old "any live booking blocks the venue forever"
        behaviour (see `is_booked`) with a real per-slot collision check:
        a venue booked Monday 2-3pm should still be bookable Tuesday, or
        Monday 4-5pm, or next month.
        """
        qs = self.bookings.filter(is_current=True)
        if exclude_booking_id:
            qs = qs.exclude(pk=exclude_booking_id)

        for existing in qs:
            if _same_occasion(existing.date, existing.day, date, day) and _times_overlap(
                existing.start_time, existing.end_time, start_time, end_time
            ):
                return existing
        return None


def _same_occasion(existing_date, existing_day, new_date, new_day):
    """Do two bookings fall on the same calendar day/occasion?"""
    if existing_date and new_date:
        return existing_date == new_date
    # Fall back to weekday name when either side only recorded a day-of-week
    existing_weekday = existing_date.strftime("%A") if existing_date else (existing_day or "")
    new_weekday = new_date.strftime("%A") if new_date else (new_day or "")
    if existing_weekday and new_weekday:
        return existing_weekday == new_weekday
    # Neither booking has enough day information to compare — be conservative
    return True


def _times_overlap(existing_start, existing_end, new_start, new_end):
    """Do two [start, end) time ranges actually overlap?"""
    if not (existing_start and existing_end and new_start and new_end):
        # Missing time info on either side — can't prove they DON'T overlap,
        # so be conservative rather than silently double-booking.
        return True
    return existing_start < new_end and new_start < existing_end


DAY_CHOICES = [
    ("Monday", "Monday"),
    ("Tuesday", "Tuesday"),
    ("Wednesday", "Wednesday"),
    ("Thursday", "Thursday"),
    ("Friday", "Friday"),
    ("Saturday", "Saturday"),
    ("Sunday", "Sunday"),
]


class MeetingBooking(models.Model):
    """
    A booking record against a MeetingVenue.

    Only one booking per venue is ever "current" (is_current=True) at a time —
    that is the live/active state the Utility Officer sees on the panel.
    Unbooking marks the current booking as cancelled. Transferring a meeting
    closes out the current booking (status='transferred') and opens a new
    current booking — on the same or a different venue/day/time — linked back
    via `previous_booking`, so the full move history is preserved.
    """

    STATUS_CHOICES = [
        ("booked", "Booked"),
        ("cancelled", "Cancelled (Unbooked)"),
        ("transferred", "Transferred"),
        ("completed", "Completed"),
    ]

    venue = models.ForeignKey(MeetingVenue, on_delete=models.CASCADE, related_name="bookings")

    booked_by = models.CharField(
        max_length=200,
        help_text="Name / department / office of whoever booked this venue.",
    )
    booked_for = models.CharField(
        max_length=255,
        help_text="Purpose or title of the meeting, e.g. 'Senate Meeting', 'Staff Briefing'.",
    )
    contact = models.CharField(max_length=150, blank=True, default="")

    date = models.DateField(null=True, blank=True)
    day = models.CharField(max_length=20, choices=DAY_CHOICES, blank=True, default="")
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="booked")
    is_current = models.BooleanField(
        default=True,
        help_text="Whether this is the venue's live/active booking.",
    )
    notes = models.TextField(blank=True, default="")

    previous_booking = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="next_bookings",
        help_text="Set automatically when this booking was created by transferring a prior one.",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="meeting_bookings_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Meeting Booking"
        verbose_name_plural = "Meeting Bookings"

    def __str__(self):
        return f"{self.venue.code} booked by {self.booked_by} for {self.booked_for}"


class UnbookRequest(models.Model):
    """
    A request — typically submitted by a staff member from the public
    booking page, without needing to log in — to cancel/unbook a live
    MeetingBooking. Does NOT unbook anything by itself; it must be
    approved by a Utility Officer from the Meeting Venues panel.
    """

    STATUS_CHOICES = [
        ("pending", "Pending Approval"),
        ("approved", "Approved (Unbooked)"),
        ("rejected", "Rejected"),
    ]

    booking = models.ForeignKey(
        MeetingBooking, on_delete=models.CASCADE, related_name="unbook_requests",
    )
    requested_by = models.CharField(max_length=200)
    contact = models.CharField(max_length=150, blank=True, default="")
    reason = models.TextField(blank=True, default="")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    requested_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="unbook_requests_resolved",
    )
    resolution_note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = "Unbook Request"
        verbose_name_plural = "Unbook Requests"

    def __str__(self):
        return f"Unbook request for booking #{self.booking_id} ({self.status})"
