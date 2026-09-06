import datetime

from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.rbac import allowed_roles, Role

from .models import MeetingVenue, MeetingBooking, UnbookRequest, DAY_CHOICES


def _parse_time(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None


def _parse_date(raw):
    """Accepts a date object/None (already parsed) or an 'YYYY-MM-DD' string
    from a POST body, and always returns a real datetime.date or None."""
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime.date):
        return raw
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _fmt_time(t):
    return t.strftime("%H:%M") if t else ""


def _serialize_venue(venue):
    booking = venue.current_booking
    return {
        "id": venue.id,
        "code": venue.code,
        "name": venue.name or "",
        "location": venue.location or "",
        "capacity": venue.capacity if venue.capacity is not None else "",
        "venue_type": venue.venue_type,
        "venue_type_display": venue.get_venue_type_display(),
        "is_workshop": venue.is_workshop,
        "is_lab": venue.is_lab,
        "description": venue.description or "",
        "is_active": venue.is_active,
        "is_booked": booking is not None,
        "booking": _serialize_booking(booking) if booking else None,
    }


def _serialize_booking(booking):
    if not booking:
        return None
    return {
        "id": booking.id,
        "venue_id": booking.venue_id,
        "venue_code": booking.venue.code,
        "booked_by": booking.booked_by,
        "booked_for": booking.booked_for,
        "contact": booking.contact or "",
        "date": booking.date.isoformat() if booking.date else "",
        "day": booking.day or "",
        "start_time": _fmt_time(booking.start_time),
        "end_time": _fmt_time(booking.end_time),
        "status": booking.status,
        "notes": booking.notes or "",
    }


@allowed_roles(Role.UTILITY, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["GET", "POST"])
def meeting_venues_panel(request):
    """
    GET: render the Meeting Venues panel (CRUD for meeting venues + the
         live booking board with book / unbook / transfer actions).
    POST (AJAX): handle every action listed below via `action`.
    """
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action = (request.POST.get("action") or "").strip().lower()

        # ------------------------------------------------------------ #
        #  MEETING VENUE CRUD                                          #
        # ------------------------------------------------------------ #
        if action in ("create_venue", "update_venue"):
            venue_id = request.POST.get("id")
            code = (request.POST.get("code") or "").strip().upper()
            name = (request.POST.get("name") or "").strip()
            location = (request.POST.get("location") or "").strip()
            venue_type = (request.POST.get("venue_type") or "hall").strip()
            is_workshop = request.POST.get("is_workshop") == "true"
            is_lab = request.POST.get("is_lab") == "true"
            is_active = request.POST.get("is_active", "true") == "true"
            description = (request.POST.get("description") or "").strip()

            if not code:
                return JsonResponse({"status": "error", "message": "Venue code is required."}, status=400)

            valid_types = {c[0] for c in MeetingVenue.VENUE_TYPE_CHOICES}
            if venue_type not in valid_types:
                venue_type = "hall"

            try:
                capacity = int(request.POST.get("capacity"))
            except (TypeError, ValueError):
                capacity = None

            if venue_id:
                venue = get_object_or_404(MeetingVenue, pk=venue_id)
                if MeetingVenue.objects.exclude(pk=venue.pk).filter(code__iexact=code).exists():
                    return JsonResponse({"status": "error", "message": "A meeting venue with that code already exists."}, status=400)
                venue.code = code
                venue.name = name
                venue.location = location
                venue.capacity = capacity
                venue.venue_type = venue_type
                venue.is_workshop = is_workshop
                venue.is_lab = is_lab
                venue.is_active = is_active
                venue.description = description
                venue.save()
            else:
                if MeetingVenue.objects.filter(code__iexact=code).exists():
                    return JsonResponse({"status": "error", "message": "A meeting venue with that code already exists."}, status=400)
                venue = MeetingVenue.objects.create(
                    code=code, name=name, location=location, capacity=capacity,
                    venue_type=venue_type, is_workshop=is_workshop, is_lab=is_lab,
                    is_active=is_active, description=description,
                )

            return JsonResponse({"status": "success", "venue": _serialize_venue(venue)})

        elif action == "delete_venue":
            venue = get_object_or_404(MeetingVenue, pk=request.POST.get("id"))
            pk = venue.pk
            venue.delete()
            return JsonResponse({"status": "success", "id": pk})

        # ------------------------------------------------------------ #
        #  BOOK A VENUE                                                 #
        # ------------------------------------------------------------ #
        elif action == "book":
            venue = get_object_or_404(MeetingVenue, pk=request.POST.get("venue_id"))

            req_date = _parse_date(request.POST.get("date"))
            req_day = (request.POST.get("day") or "").strip()
            req_start = _parse_time(request.POST.get("start_time"))
            req_end = _parse_time(request.POST.get("end_time"))

            conflict = venue.conflicting_booking(date=req_date, day=req_day, start_time=req_start, end_time=req_end)
            if conflict:
                when = conflict.date.strftime("%d %b %Y") if conflict.date else (conflict.day or "that day")
                return JsonResponse({
                    "status": "error",
                    "message": f"{venue.code} is already booked for {when} "
                               f"({_fmt_time(conflict.start_time)}-{_fmt_time(conflict.end_time)}). "
                               "Unbook/transfer that booking first, or choose a different time.",
                }, status=400)

            booked_by = (request.POST.get("booked_by") or "").strip()
            booked_for = (request.POST.get("booked_for") or "").strip()
            if not booked_by or not booked_for:
                return JsonResponse({"status": "error", "message": "Please provide who booked the venue and what it's for."}, status=400)

            booking = MeetingBooking.objects.create(
                venue=venue,
                booked_by=booked_by,
                booked_for=booked_for,
                contact=(request.POST.get("contact") or "").strip(),
                date=req_date,
                day=req_day,
                start_time=req_start,
                end_time=req_end,
                notes=(request.POST.get("notes") or "").strip(),
                status="booked",
                is_current=True,
                created_by=request.user if request.user.is_authenticated else None,
            )
            return JsonResponse({"status": "success", "venue": _serialize_venue(venue), "booking": _serialize_booking(booking)})

        # ------------------------------------------------------------ #
        #  UNBOOK A VENUE                                              #
        # ------------------------------------------------------------ #
        elif action == "unbook":
            venue = get_object_or_404(MeetingVenue, pk=request.POST.get("venue_id"))
            booking = venue.current_booking
            if not booking:
                return JsonResponse({"status": "error", "message": f"{venue.code} is not currently booked."}, status=400)

            booking.is_current = False
            booking.status = "cancelled"
            booking.save(update_fields=["is_current", "status", "updated_at"])

            return JsonResponse({"status": "success", "venue": _serialize_venue(venue)})

        # ------------------------------------------------------------ #
        #  TRANSFER A MEETING — to another venue and/or day/time        #
        # ------------------------------------------------------------ #
        elif action == "transfer":
            booking = get_object_or_404(MeetingBooking, pk=request.POST.get("booking_id"), is_current=True)
            new_venue_id = request.POST.get("new_venue_id") or booking.venue_id
            new_venue = get_object_or_404(MeetingVenue, pk=new_venue_id)

            new_date = _parse_date(request.POST.get("date")) or booking.date
            new_day = (request.POST.get("day") or booking.day)
            new_start = _parse_time(request.POST.get("start_time")) or booking.start_time
            new_end = _parse_time(request.POST.get("end_time")) or booking.end_time

            conflict = new_venue.conflicting_booking(
                date=new_date, day=new_day, start_time=new_start, end_time=new_end,
                exclude_booking_id=booking.pk,
            )
            if conflict:
                when = conflict.date.strftime("%d %b %Y") if conflict.date else (conflict.day or "that day")
                return JsonResponse({
                    "status": "error",
                    "message": f"{new_venue.code} is already booked for {when} "
                               f"({_fmt_time(conflict.start_time)}-{_fmt_time(conflict.end_time)}) — pick a free venue or time.",
                }, status=400)

            # Close out the old booking
            booking.is_current = False
            booking.status = "transferred"
            booking.save(update_fields=["is_current", "status", "updated_at"])

            # Open the new one, carrying over details unless overridden
            new_booking = MeetingBooking.objects.create(
                venue=new_venue,
                booked_by=(request.POST.get("booked_by") or booking.booked_by).strip(),
                booked_for=(request.POST.get("booked_for") or booking.booked_for).strip(),
                contact=(request.POST.get("contact") or booking.contact),
                date=new_date,
                day=new_day,
                start_time=new_start,
                end_time=new_end,
                notes=(request.POST.get("notes") or booking.notes),
                status="booked",
                is_current=True,
                previous_booking=booking,
                created_by=request.user if request.user.is_authenticated else None,
            )

            return JsonResponse({
                "status": "success",
                "old_venue": _serialize_venue(booking.venue),
                "new_venue": _serialize_venue(new_venue),
                "booking": _serialize_booking(new_booking),
            })

        # ------------------------------------------------------------ #
        #  APPROVE / REJECT A PUBLIC UNBOOK REQUEST                    #
        # ------------------------------------------------------------ #
        elif action == "approve_unbook_request":
            req = get_object_or_404(UnbookRequest, pk=request.POST.get("request_id"), status="pending")
            booking = req.booking

            if booking.is_current:
                booking.is_current = False
                booking.status = "cancelled"
                booking.save(update_fields=["is_current", "status", "updated_at"])

            req.status = "approved"
            req.resolved_at = timezone.now()
            req.resolved_by = request.user if request.user.is_authenticated else None
            req.resolution_note = (request.POST.get("resolution_note") or "").strip()
            req.save(update_fields=["status", "resolved_at", "resolved_by", "resolution_note"])

            return JsonResponse({"status": "success", "request_id": req.id, "venue": _serialize_venue(booking.venue)})

        elif action == "reject_unbook_request":
            req = get_object_or_404(UnbookRequest, pk=request.POST.get("request_id"), status="pending")
            req.status = "rejected"
            req.resolved_at = timezone.now()
            req.resolved_by = request.user if request.user.is_authenticated else None
            req.resolution_note = (request.POST.get("resolution_note") or "").strip()
            req.save(update_fields=["status", "resolved_at", "resolved_by", "resolution_note"])

            return JsonResponse({"status": "success", "request_id": req.id})

        return JsonResponse({"status": "error", "message": "Unknown action."}, status=400)

    # ---------------------------------------------------------------- #
    #  GET – render the full panel                                     #
    # ---------------------------------------------------------------- #
    venues = MeetingVenue.objects.all().order_by("code")
    active_bookings = (
        MeetingBooking.objects.filter(is_current=True)
        .select_related("venue")
        .order_by("venue__code")
    )
    pending_requests = (
        UnbookRequest.objects.filter(status="pending")
        .select_related("booking", "booking__venue")
        .order_by("-requested_at")
    )

    return render(request, "meeting_venues/panel.html", {
        "venues": venues,
        "active_bookings": active_bookings,
        "pending_requests": pending_requests,
        "venue_type_choices": MeetingVenue.VENUE_TYPE_CHOICES,
        "day_choices": DAY_CHOICES,
    })


# ==================================================================== #
#  PUBLIC, NO-LOGIN-REQUIRED VIEWS                                     #
#  Linked from the Staff Portal so any staff member can self-serve     #
#  a booking without an account. Unbooking, however, only ever         #
#  happens through a *request* that a Utility Officer must approve.    #
# ==================================================================== #

def public_book_meeting_venue(request):
    """
    Public self-service booking form — no login required.
    Reuses the same "one live booking per venue" collision check as the
    officer's own Book action, so two people can never grab the same
    venue for the same live slot.
    """
    venues = MeetingVenue.objects.filter(is_active=True).order_by("code")
    reference = None

    if request.method == "POST":
        venue_id = request.POST.get("venue_id")
        if not venue_id:
            messages.error(request, "Please select a venue first.")
            return render(request, "meeting_venues/public_book.html", _public_book_context(venues, None))

        venue = get_object_or_404(MeetingVenue, pk=venue_id, is_active=True)
        booked_by = (request.POST.get("booked_by") or "").strip()
        booked_for = (request.POST.get("booked_for") or "").strip()

        req_date = _parse_date(request.POST.get("date"))
        req_day = (request.POST.get("day") or "").strip()
        req_start = _parse_time(request.POST.get("start_time"))
        req_end = _parse_time(request.POST.get("end_time"))

        conflict = venue.conflicting_booking(date=req_date, day=req_day, start_time=req_start, end_time=req_end)

        if conflict:
            when = conflict.date.strftime("%d %b %Y") if conflict.date else (conflict.day or "that day")
            messages.error(
                request,
                f"Sorry — {venue.code} is already booked for {when} "
                f"({_fmt_time(conflict.start_time)}-{_fmt_time(conflict.end_time)}). "
                "Please choose another venue or a different time.",
            )
        elif not booked_by or not booked_for:
            messages.error(request, "Please tell us your name/department and the purpose of the meeting.")
        else:
            booking = MeetingBooking.objects.create(
                venue=venue,
                booked_by=booked_by,
                booked_for=booked_for,
                contact=(request.POST.get("contact") or "").strip(),
                date=req_date,
                day=req_day,
                start_time=req_start,
                end_time=req_end,
                notes=(request.POST.get("notes") or "").strip(),
                status="booked",
                is_current=True,
            )
            reference = booking.id
            messages.success(
                request,
                f"{venue.code} has been booked for you. Your booking reference is #{reference} — "
                "keep it safe, you'll need it if you ever want to request an unbooking.",
            )
            venues = MeetingVenue.objects.filter(is_active=True).order_by("code")  # refresh statuses

    return render(request, "meeting_venues/public_book.html", _public_book_context(venues, reference))


def _public_book_context(venues, reference):
    venues = list(venues)
    booked_count = sum(1 for v in venues if v.is_booked)
    return {
        "venues": venues,
        "day_choices": DAY_CHOICES,
        "reference": reference,
        "available_count": len(venues) - booked_count,
        "booked_count": booked_count,
    }


def public_request_unbook(request):
    """
    Public "cancel my booking" form — no login required. Does NOT unbook
    anything directly; it creates a pending UnbookRequest that a Utility
    Officer must approve from the Meeting Venues panel.
    """
    booking = None
    already_pending = False

    if request.method == "POST":
        ref = (request.POST.get("reference") or "").strip()
        requested_by = (request.POST.get("requested_by") or "").strip()
        reason = (request.POST.get("reason") or "").strip()

        try:
            ref_id = int(ref)
        except (TypeError, ValueError):
            ref_id = None

        booking = (
            MeetingBooking.objects.filter(pk=ref_id, is_current=True, status="booked").select_related("venue").first()
            if ref_id is not None else None
        )

        if not booking:
            messages.error(request, "We couldn't find an active booking with that reference number.")
        elif not requested_by:
            messages.error(request, "Please tell us your name so the Utility Office knows who is requesting this.")
        elif booking.unbook_requests.filter(status="pending").exists():
            messages.info(request, "An unbook request for this booking is already pending approval.")
        else:
            UnbookRequest.objects.create(
                booking=booking,
                requested_by=requested_by,
                contact=(request.POST.get("contact") or "").strip(),
                reason=reason,
            )
            messages.success(
                request,
                f"Your request to unbook {booking.venue.code} (reference #{booking.id}) has been sent to the "
                "Utility Office for approval. It will remain booked until they approve it.",
            )
            booking = None

    return render(request, "meeting_venues/public_unbook_request.html", {"booking": booking})
