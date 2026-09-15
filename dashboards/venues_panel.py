# venues_panel.py
# ===================================================================
#  FULL PRODUCTION-READY VENUES PANEL VIEW
#  Includes all functionality from the original file plus the updated
#  multi-day/multi-timeslot Lecturer Time Preferences
#
#  IMPORTS:
#  - Lecturer constraints are imported from timetable.models
#  - Lecturer model is imported from lecturer_portal.models
#  - Core models for SchedulerConstraintToggle are imported from core.models
# ===================================================================

import datetime

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse

# ── Room Management ──────────────────────────────────────────────────────────
from room_management.models import Building, Venue, LabVenue, VenueSpecialization, VenueBlock

# ── Resits ──────────────────────────────────────────────────────────────────
from resits_timetabling.models import ResitVenueExclusion

# ── Program & Department ────────────────────────────────────────────────────
from program_management.models import Program, ProgramCourse
from department_management.models import Department

# ── Core ────────────────────────────────────────────────────────────────────
from core.rbac import allowed_roles, Role
from core.models import SchedulerConstraintToggle
from core.scheduling_constraints import ensure_constraint_toggles, CONSTRAINT_DEFS

# ── Timetable (Lecturer Constraints are here) ─────────────────────────────
from timetable.models import (
    LecturerBlockedSlot,
    LecturerTimePreference,
    LecturerTimePreferenceSlot,
    LecturerVenuePreference,
    SchedulerConfig,
)

# ── Lecturer Portal ─────────────────────────────────────────────────────────
from lecturer_portal.models import Lecturer

# ── Special Requests ────────────────────────────────────────────────────────
from special_requests.models import SpecialRequest
from special_requests.services import (
    list_special_requests_by_department,
    mark_special_request_constraint_applied,
)


# ── Helper functions ──────────────────────────────────────────────────────────

def _parse_time(raw):
    """
    Parse a time string in HH:MM format.
    Returns None if the string is empty or invalid.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None


def _fmt_time(t):
    """Format a time object as HH:MM string, or empty string if None."""
    return t.strftime("%H:%M") if t else ""


def _get_available_timeslots():
    """
    Return a list of (start_time_str, end_time_str) tuples from the SchedulerConfig.
    Used to populate the timeslot checkboxes in the lecturer time preference form.

    If SchedulerConfig is not available, returns a sensible default based on
    the standard university timetable (7 AM - 7 PM with 3-hour slots).
    """
    try:
        from timetable.algorithms.regular_timetable_autosheduler_algorithm import generate_slots

        config = SchedulerConfig.objects.first()
        if not config:
            # Fallback to default 3-hour slots from 7 AM to 7 PM
            fallback_slots = [
                ("07:00", "10:00"),
                ("10:00", "13:00"),
                ("14:00", "17:00"),
                ("17:00", "19:00"),
            ]
            return fallback_slots

        slots = generate_slots(config.start_time, config.end_time, config.slot_size)
        # Convert time objects to string format for JSON serialization
        return [(st.strftime("%H:%M"), et.strftime("%H:%M")) for st, et in slots]

    except ImportError:
        # Fallback if the algorithm module can't be imported
        return [("07:00", "10:00"), ("10:00", "13:00"), ("14:00", "17:00"), ("17:00", "19:00")]
    except Exception:
        # Any other error, return a safe default
        return [("07:00", "10:00"), ("10:00", "13:00"), ("14:00", "17:00"), ("17:00", "19:00")]


# ── Serialization functions ──────────────────────────────────────────────────

def _serialize_spec(spec, detail=False):
    """
    Serialize a VenueSpecialization for JSON response.
    """
    data = {
        "id": spec.id,
        "name": spec.name,
        "notes": spec.notes or "",
        "venue_ids": list(spec.venues.values_list("id", flat=True)),
        "venue_codes": [v.code for v in spec.venues.all()],
        "prog_count": spec.programs.count(),
        "course_count": spec.courses.count(),
        "prog_names": [p.name for p in spec.programs.all()],
        "course_labels": [
            f"{c.course_code} — {c.course_name}"
            for c in spec.courses.all()
        ],
        "strict": spec.strict,
        "exclusive": spec.exclusive,
    }
    if detail:
        data["program_ids"] = list(spec.programs.values_list("id", flat=True))
        data["course_ids"] = list(spec.courses.values_list("id", flat=True))
    return data


def _serialize_lecturer_block(row):
    """
    Serialize a LecturerBlockedSlot for JSON response.
    """
    return {
        "id": row.id,
        "lecturer_id": row.lecturer_id,
        "lecturer_name": row.lecturer.name,
        "day": row.day,
        "start_time": _fmt_time(row.start_time),
        "end_time": _fmt_time(row.end_time),
        "reason": row.reason or "",
        "is_active": row.is_active,
    }


def _serialize_time_pref(preference, detail=False):
    """
    Serialize a LecturerTimePreference for JSON response.
    Supports the new multi-day/multi-timeslot structure.

    Args:
        preference: LecturerTimePreference instance
        detail: If True, include full slot details

    Returns:
        dict with serialized data
    """
    slots_by_day = {}
    for slot in preference.slots.all().order_by('day', 'start_time'):
        day = slot.day
        if day not in slots_by_day:
            slots_by_day[day] = []
        if slot.is_whole_day:
            slots_by_day[day].append({"type": "whole_day"})
        else:
            slots_by_day[day].append({
                "type": "timeslot",
                "start": slot.start_time.strftime("%H:%M") if slot.start_time else None,
                "end": slot.end_time.strftime("%H:%M") if slot.end_time else None,
            })

    data = {
        "id": preference.id,
        "lecturer_id": preference.lecturer_id,
        "lecturer_name": preference.lecturer.name,
        "slots_by_day": slots_by_day,
        "day_summary": preference.get_formatted_summary(),
        "notes": preference.notes or "",
        "is_active": preference.is_active,
    }

    if detail:
        data["slots"] = [
            {
                "id": slot.id,
                "day": slot.day,
                "start_time": slot.start_time.strftime("%H:%M") if slot.start_time else None,
                "end_time": slot.end_time.strftime("%H:%M") if slot.end_time else None,
                "is_whole_day": slot.is_whole_day,
            }
            for slot in preference.slots.all()
        ]

    return data


def _serialize_venue_pref(row):
    """
    Serialize a LecturerVenuePreference for JSON response.
    """
    return {
        "id": row.id,
        "lecturer_id": row.lecturer_id,
        "lecturer_name": row.lecturer.name,
        "venue_ids": list(row.venues.values_list("id", flat=True)),
        "venue_codes": [v.code for v in row.venues.all()],
        "notes": row.notes or "",
        "is_active": row.is_active,
    }


def _serialize_resit_exclusion(excl):
    """
    Serialize a ResitVenueExclusion for JSON response.
    """
    if excl.building_id:
        return {
            "id": excl.id,
            "scope": "building",
            "building_id": excl.building_id,
            "building_name": excl.building.name,
            "venue_id": None,
            "venue_code": None,
            "reason": excl.reason or "",
            "is_active": excl.is_active,
        }
    return {
        "id": excl.id,
        "scope": "venue",
        "building_id": None,
        "building_name": None,
        "venue_id": excl.venue_id,
        "venue_code": excl.venue.code,
        "reason": excl.reason or "",
        "is_active": excl.is_active,
    }


# ── Main view ─────────────────────────────────────────────────────────────────

@allowed_roles(Role.UTILITY, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def venues_panel(request):
    """
    Main venues panel view.
    Handles both GET (render the panel) and POST (AJAX CRUD operations).
    """
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action = request.POST.get("action")
        model_type = request.POST.get("model_type")

        # ------------------------------------------------------------------ #
        #  BUILDING CRUD                                                      #
        # ------------------------------------------------------------------ #
        if model_type == "building":
            if action == "create":
                building_id = request.POST.get("id")
                name = request.POST.get("name", "").strip()
                code = request.POST.get("code", "").strip().upper()
                description = request.POST.get("description", "").strip()
                is_workshop = request.POST.get("is_workshop") == "true"

                if not name or not code:
                    return JsonResponse({"status": "error", "message": "Name and code are required."}, status=400)

                if building_id:
                    building = get_object_or_404(Building, pk=building_id)
                    building.name = name
                    building.code = code
                    building.description = description
                    building.is_workshop = is_workshop
                    building.save()
                else:
                    building = Building.objects.create(
                        name=name,
                        code=code,
                        description=description,
                        is_workshop=is_workshop,
                    )

                return JsonResponse({
                    "status": "success",
                    "building": {
                        "id": building.id,
                        "name": building.name,
                        "code": building.code,
                        "description": building.description or "",
                        "is_workshop": building.is_workshop,
                    }
                })

            elif action == "delete":
                building = get_object_or_404(Building, pk=request.POST.get("id"))
                pk = building.pk
                building.delete()
                return JsonResponse({"status": "success", "id": pk})

        # ------------------------------------------------------------------ #
        #  VENUE CRUD                                                        #
        # ------------------------------------------------------------------ #
        elif model_type == "venue":
            if action == "create":
                venue_id = request.POST.get("id")
                code = request.POST.get("code", "").strip().upper()
                building_id = request.POST.get("building_id") or None
                is_workshop = request.POST.get("is_workshop") == "true"
                is_specialized = request.POST.get("is_specialized") == "true"
                description = request.POST.get("description", "").strip()

                try:
                    capacity = int(request.POST.get("capacity"))
                except (TypeError, ValueError):
                    capacity = None
                try:
                    exam_capacity = int(request.POST.get("exam_capacity"))
                except (TypeError, ValueError):
                    exam_capacity = None

                building = get_object_or_404(Building, pk=building_id) if building_id else None

                if venue_id:
                    venue = get_object_or_404(Venue, pk=venue_id)
                    venue.code = code
                    venue.building = building
                    venue.capacity = capacity
                    venue.exam_capacity = exam_capacity
                    venue.description = description
                    venue.is_workshop = is_workshop
                    venue.is_specialized = is_specialized
                    venue.save()
                else:
                    venue = Venue.objects.create(
                        code=code,
                        building=building,
                        capacity=capacity,
                        exam_capacity=exam_capacity,
                        description=description,
                        is_workshop=is_workshop,
                        is_specialized=is_specialized,
                    )

                return JsonResponse({
                    "status": "success",
                    "venue": {
                        "id": venue.id,
                        "code": venue.code,
                        "building_id": venue.building.id if venue.building else "",
                        "building_name": venue.building.name if venue.building else "—",
                        "capacity": venue.capacity if venue.capacity is not None else "",
                        "exam_capacity": venue.exam_capacity if venue.exam_capacity is not None else "",
                        "description": venue.description or "",
                        "is_workshop": venue.is_workshop,
                        "is_specialized": venue.is_specialized,
                    }
                })

            elif action == "delete":
                venue = get_object_or_404(Venue, pk=request.POST.get("id"))
                pk = venue.pk
                venue.delete()
                return JsonResponse({"status": "success", "id": pk})

        # ------------------------------------------------------------------ #
        #  LAB VENUE CRUD                                                    #
        # ------------------------------------------------------------------ #
        elif model_type == "lab_venue":
            if action == "create":
                lab_id = request.POST.get("id")
                code = request.POST.get("code", "").strip().upper()
                description = request.POST.get("description", "").strip()
                equipment = request.POST.get("equipment", "").strip()

                try:
                    capacity = int(request.POST.get("capacity"))
                except (TypeError, ValueError):
                    capacity = None

                if not code:
                    return JsonResponse({"status": "error", "message": "Code is required."}, status=400)

                if lab_id:
                    lab = get_object_or_404(LabVenue, pk=lab_id)
                    lab.code = code
                    lab.capacity = capacity
                    lab.description = description
                    lab.equipment = equipment
                    lab.save()
                else:
                    lab = LabVenue.objects.create(
                        code=code,
                        capacity=capacity,
                        description=description,
                        equipment=equipment,
                    )

                return JsonResponse({
                    "status": "success",
                    "lab": {
                        "id": lab.id,
                        "code": lab.code,
                        "capacity": lab.capacity if lab.capacity is not None else "",
                        "description": lab.description or "",
                        "equipment": lab.equipment or "",
                    }
                })

            elif action == "delete":
                lab = get_object_or_404(LabVenue, pk=request.POST.get("id"))
                pk = lab.pk
                lab.delete()
                return JsonResponse({"status": "success", "id": pk})

        # ------------------------------------------------------------------ #
        #  VENUE SPECIALIZATION CRUD                                         #
        # ------------------------------------------------------------------ #
        elif model_type == "specialization":
            if action == "create":
                spec_id = request.POST.get("id")
                name = request.POST.get("name", "").strip()
                notes = request.POST.get("notes", "").strip()
                venue_ids = request.POST.getlist("venue_ids[]")
                program_ids = request.POST.getlist("program_ids[]")
                course_ids = request.POST.getlist("course_ids[]")
                strict = request.POST.get("strict") == "true"
                exclusive = request.POST.get("exclusive") == "true"

                if not name:
                    return JsonResponse({"status": "error", "message": "Rule name is required."}, status=400)
                if not venue_ids:
                    return JsonResponse({"status": "error", "message": "Select at least one venue."}, status=400)
                if not program_ids and not course_ids:
                    return JsonResponse({"status": "error", "message": "Select at least one program or course."}, status=400)

                if spec_id:
                    spec = get_object_or_404(VenueSpecialization, pk=spec_id)
                    spec.name = name
                    spec.notes = notes
                    spec.strict = strict
                    spec.exclusive = exclusive
                    spec.save()
                else:
                    spec = VenueSpecialization.objects.create(
                        name=name,
                        notes=notes,
                        strict=strict,
                        exclusive=exclusive,
                    )

                spec.venues.set(Venue.objects.filter(pk__in=venue_ids))
                spec.programs.set(Program.objects.filter(pk__in=program_ids))
                spec.courses.set(ProgramCourse.objects.filter(pk__in=course_ids))
                spec.departments.clear()

                # Auto-flag linked venues as specialized
                Venue.objects.filter(pk__in=venue_ids).update(is_specialized=True)

                return JsonResponse({"status": "success", "spec": _serialize_spec(spec)})

            elif action == "delete":
                spec = get_object_or_404(VenueSpecialization, pk=request.POST.get("id"))
                venue_ids_affected = list(spec.venues.values_list("id", flat=True))
                spec.delete()
                for vid in venue_ids_affected:
                    if not VenueSpecialization.objects.filter(venues__id=vid).exists():
                        Venue.objects.filter(pk=vid).update(is_specialized=False)
                return JsonResponse({"status": "success", "id": request.POST.get("id")})

            elif action == "get":
                spec = get_object_or_404(VenueSpecialization, pk=request.POST.get("id"))
                return JsonResponse({"status": "success", "spec": _serialize_spec(spec, detail=True)})

        # ------------------------------------------------------------------ #
        #  VENUE BLOCK CRUD — hides a venue from the autoscheduler entirely   #
        # ------------------------------------------------------------------ #
        elif model_type == "venue_block":
            if action == "create":
                block_id = request.POST.get("id")
                venue_id = request.POST.get("venue_id")
                reason = request.POST.get("reason", "").strip()
                is_active = request.POST.get("is_active", "true") == "true"

                if not venue_id:
                    return JsonResponse({"status": "error", "message": "Select a venue to block."}, status=400)

                venue = get_object_or_404(Venue, pk=venue_id)

                if block_id:
                    block = get_object_or_404(VenueBlock, pk=block_id)
                    block.venue = venue
                    block.reason = reason
                    block.is_active = is_active
                    block.save()
                else:
                    block, _created = VenueBlock.objects.update_or_create(
                        venue=venue,
                        defaults={"reason": reason, "is_active": is_active},
                    )

                return JsonResponse({
                    "status": "success",
                    "block": {
                        "id": block.id,
                        "venue_id": block.venue_id,
                        "venue_code": block.venue.code,
                        "reason": block.reason or "",
                        "is_active": block.is_active,
                    },
                })

            elif action == "delete":
                block = get_object_or_404(VenueBlock, pk=request.POST.get("id"))
                pk = block.pk
                block.delete()
                return JsonResponse({"status": "success", "id": pk})

        # ------------------------------------------------------------------ #
        #  RESIT VENUE EXCLUSION CRUD — hides a venue/building from the       #
        #  RESIT autoscheduler only (independent of VenueBlock above, which  #
        #  only affects the regular/main autoscheduler).                     #
        # ------------------------------------------------------------------ #
        elif model_type == "resit_venue_exclusion":
            if action == "create":
                excl_id = request.POST.get("id")
                scope = request.POST.get("scope", "venue")   # "building" | "venue"
                building_id = request.POST.get("building_id") or None
                venue_id = request.POST.get("venue_id") or None
                reason = request.POST.get("reason", "").strip()
                is_active = request.POST.get("is_active", "true") == "true"

                if scope == "building":
                    if not building_id:
                        return JsonResponse({"status": "error", "message": "Select a building to exclude."}, status=400)
                    building = get_object_or_404(Building, pk=building_id)
                    venue = None
                else:
                    if not venue_id:
                        return JsonResponse({"status": "error", "message": "Select a venue to exclude."}, status=400)
                    venue = get_object_or_404(Venue, pk=venue_id)
                    building = None

                if excl_id:
                    excl = get_object_or_404(ResitVenueExclusion, pk=excl_id)
                    excl.building = building
                    excl.venue = venue
                    excl.reason = reason
                    excl.is_active = is_active
                    excl.created_by = excl.created_by or request.user
                    excl.save()
                else:
                    defaults = {
                        "reason": reason,
                        "is_active": is_active,
                        "created_by": request.user,
                    }
                    if building is not None:
                        excl, _created = ResitVenueExclusion.objects.update_or_create(
                            building=building,
                            venue=None,
                            defaults=defaults,
                        )
                    else:
                        excl, _created = ResitVenueExclusion.objects.update_or_create(
                            venue=venue,
                            building=None,
                            defaults=defaults,
                        )

                return JsonResponse({
                    "status": "success",
                    "exclusion": _serialize_resit_exclusion(excl),
                })

            elif action == "delete":
                excl = get_object_or_404(ResitVenueExclusion, pk=request.POST.get("id"))
                pk = excl.pk
                excl.delete()
                return JsonResponse({"status": "success", "id": pk})

        # ------------------------------------------------------------------ #
        #  LECTURER BLOCKED SLOT CRUD (hard rule)                             #
        # ------------------------------------------------------------------ #
        elif model_type == "lecturer_block":
            if action == "create":
                row_id = request.POST.get("id")
                lecturer_id = request.POST.get("lecturer_id")
                day = request.POST.get("day", "").strip()
                start_time = _parse_time(request.POST.get("start_time"))
                end_time = _parse_time(request.POST.get("end_time"))
                reason = request.POST.get("reason", "").strip()
                is_active = request.POST.get("is_active", "true") == "true"

                if not lecturer_id or not day:
                    return JsonResponse({"status": "error", "message": "Lecturer and day are required."}, status=400)

                lecturer = get_object_or_404(Lecturer, pk=lecturer_id)

                if row_id:
                    row = get_object_or_404(LecturerBlockedSlot, pk=row_id)
                    row.lecturer = lecturer
                    row.day = day
                    row.start_time = start_time
                    row.end_time = end_time
                    row.reason = reason
                    row.is_active = is_active
                    row.save()
                else:
                    row = LecturerBlockedSlot.objects.create(
                        lecturer=lecturer,
                        day=day,
                        start_time=start_time,
                        end_time=end_time,
                        reason=reason,
                        is_active=is_active,
                    )

                return JsonResponse({"status": "success", "row": _serialize_lecturer_block(row)})

            elif action == "delete":
                row = get_object_or_404(LecturerBlockedSlot, pk=request.POST.get("id"))
                pk = row.pk
                row.delete()
                return JsonResponse({"status": "success", "id": pk})

        # ------------------------------------------------------------------ #
        #  LECTURER TIME PREFERENCE CRUD (soft rule) — UPDATED               #
        #  Multi-day / multi-timeslot support                                 #
        # ------------------------------------------------------------------ #
        elif model_type == "lecturer_time_pref":
            if action == "create":
                row_id = request.POST.get("id")
                lecturer_id = request.POST.get("lecturer_id")
                notes = request.POST.get("notes", "").strip()
                is_active = request.POST.get("is_active", "true") == "true"

                # Get the days and their timeslots from the form
                # Format: day_slots_{day} = list of slot indices or "whole_day"
                days_data = {}
                for key, value in request.POST.items():
                    if key.startswith("day_slots_"):
                        day = key.replace("day_slots_", "")
                        if value == "whole_day":
                            days_data[day] = ["whole_day"]
                        else:
                            # value is a comma-separated list of slot indices (e.g., "0,2")
                            slot_indices = [int(x) for x in value.split(",") if x.strip()]
                            days_data[day] = slot_indices

                if not lecturer_id:
                    return JsonResponse({"status": "error", "message": "Lecturer is required."}, status=400)

                lecturer = get_object_or_404(Lecturer, pk=lecturer_id)

                # Get available timeslots
                available_slots = _get_available_timeslots()

                if row_id:
                    # Edit existing preference — delete old slots and create new ones
                    preference = get_object_or_404(LecturerTimePreference, pk=row_id)
                    preference.lecturer = lecturer
                    preference.notes = notes
                    preference.is_active = is_active
                    preference.save()
                    # Delete existing slots
                    preference.slots.all().delete()
                else:
                    preference = LecturerTimePreference.objects.create(
                        lecturer=lecturer,
                        notes=notes,
                        is_active=is_active,
                    )

                # Create new slots
                for day, slot_data in days_data.items():
                    if slot_data == ["whole_day"]:
                        # Whole day slot
                        LecturerTimePreferenceSlot.objects.create(
                            preference=preference,
                            day=day,
                            start_time=None,
                            end_time=None,
                            is_whole_day=True,
                        )
                    else:
                        # Individual timeslots
                        for idx in slot_data:
                            if idx < len(available_slots):
                                start_str, end_str = available_slots[idx]
                                start_time = datetime.datetime.strptime(start_str, "%H:%M").time()
                                end_time = datetime.datetime.strptime(end_str, "%H:%M").time()
                                LecturerTimePreferenceSlot.objects.create(
                                    preference=preference,
                                    day=day,
                                    start_time=start_time,
                                    end_time=end_time,
                                    is_whole_day=False,
                                )

                return JsonResponse({
                    "status": "success",
                    "row": _serialize_time_pref(preference),
                })

            elif action == "delete":
                preference = get_object_or_404(LecturerTimePreference, pk=request.POST.get("id"))
                pk = preference.pk
                preference.delete()
                return JsonResponse({"status": "success", "id": pk})

            elif action == "get":
                preference = get_object_or_404(LecturerTimePreference, pk=request.POST.get("id"))
                return JsonResponse({
                    "status": "success",
                    "preference": _serialize_time_pref(preference, detail=True),
                    "available_slots": _get_available_timeslots(),
                })

        # ------------------------------------------------------------------ #
        #  LECTURER VENUE PREFERENCE CRUD (soft rule)                         #
        # ------------------------------------------------------------------ #
        elif model_type == "lecturer_venue_pref":
            if action == "create":
                row_id = request.POST.get("id")
                lecturer_id = request.POST.get("lecturer_id")
                venue_ids = request.POST.getlist("venue_ids[]")
                notes = request.POST.get("notes", "").strip()
                is_active = request.POST.get("is_active", "true") == "true"

                if not lecturer_id or not venue_ids:
                    return JsonResponse({"status": "error", "message": "Lecturer and at least one venue are required."}, status=400)

                lecturer = get_object_or_404(Lecturer, pk=lecturer_id)

                if row_id:
                    row = get_object_or_404(LecturerVenuePreference, pk=row_id)
                    row.lecturer = lecturer
                    row.notes = notes
                    row.is_active = is_active
                    row.save()
                else:
                    row = LecturerVenuePreference.objects.create(
                        lecturer=lecturer,
                        notes=notes,
                        is_active=is_active,
                    )
                row.venues.set(Venue.objects.filter(pk__in=venue_ids))

                return JsonResponse({"status": "success", "row": _serialize_venue_pref(row)})

            elif action == "delete":
                row = get_object_or_404(LecturerVenuePreference, pk=request.POST.get("id"))
                pk = row.pk
                row.delete()
                return JsonResponse({"status": "success", "id": pk})

        # ------------------------------------------------------------------ #
        #  SR (SPECIAL REQUEST) -> SCHEDULER CONSTRAINT                       #
        #  Lets the director turn a COD's SR straight into an actual         #
        #  autoscheduler constraint row, prioritised for the SR's own scope: #
        #  a "this lecturer" SR offers Lecturer Blocked Days/Times first,    #
        #  then Lecturer Day/Time Preference, then Lecturer Venue Preference.#
        # ------------------------------------------------------------------ #
        elif model_type == "sr_constraint":
            if action == "options":
                sr = get_object_or_404(SpecialRequest, pk=request.POST.get("sr_id"))

                # Constraint types offered, ordered by how likely they are
                # to be what the director wants given the SR's scope.
                if sr.scope == SpecialRequest.SCOPE_LECTURER:
                    order = [
                        SpecialRequest.CONSTRAINT_LECTURER_BLOCK,
                        SpecialRequest.CONSTRAINT_LECTURER_TIME_PREF,
                        SpecialRequest.CONSTRAINT_LECTURER_VENUE_PREF,
                        SpecialRequest.CONSTRAINT_VENUE_BLOCK,
                        SpecialRequest.CONSTRAINT_VENUE_SPECIALIZATION,
                        SpecialRequest.CONSTRAINT_VENUE_EXCLUSIVE,
                    ]
                else:
                    # Unit / program / program-courses SRs are usually about
                    # a venue need (reserve/block a room for this course or
                    # program) rather than a lecturer rule, so lead with the
                    # venue-side options; lecturer options stay available
                    # further down in case the SR names a lecturer too.
                    order = [
                        SpecialRequest.CONSTRAINT_VENUE_SPECIALIZATION,
                        SpecialRequest.CONSTRAINT_VENUE_EXCLUSIVE,
                        SpecialRequest.CONSTRAINT_VENUE_BLOCK,
                        SpecialRequest.CONSTRAINT_LECTURER_TIME_PREF,
                        SpecialRequest.CONSTRAINT_LECTURER_VENUE_PREF,
                        SpecialRequest.CONSTRAINT_LECTURER_BLOCK,
                    ]

                return JsonResponse({
                    "status": "success",
                    "sr": {
                        "id": sr.id,
                        "scope": sr.scope,
                        "scope_display": sr.get_scope_display(),
                        "description": sr.description,
                        "lecturer_id": sr.lecturer_id,
                        "lecturer_name": sr.lecturer.display_name if sr.lecturer else "",
                        "program_name": sr.program.name if sr.program else "",
                        "course_code": sr.course_code,
                        "affected_courses": sr.affected_courses,
                        "constraint_applied_type": sr.constraint_applied_type,
                        "constraint_applied_id": sr.constraint_applied_id,
                    },
                    "constraint_type_order": order,
                    "constraint_type_labels": dict(SpecialRequest.CONSTRAINT_TYPE_CHOICES),
                    "day_choices": LecturerBlockedSlot.DAY_CHOICES,
                    "lecturers": [
                        {"id": l.id, "name": l.display_name}
                        for l in Lecturer.objects.all().order_by("name")
                    ],
                    "venues": [
                        {"id": v.id, "code": v.code}
                        for v in Venue.objects.all().order_by("code")
                    ],
                })

            elif action == "apply":
                sr = get_object_or_404(SpecialRequest, pk=request.POST.get("sr_id"))
                constraint_type = request.POST.get("constraint_type", "")

                if constraint_type not in dict(SpecialRequest.CONSTRAINT_TYPE_CHOICES):
                    return JsonResponse({"status": "error", "message": "Invalid constraint type."}, status=400)

                VENUE_TYPES = (
                    SpecialRequest.CONSTRAINT_VENUE_BLOCK,
                    SpecialRequest.CONSTRAINT_VENUE_SPECIALIZATION,
                    SpecialRequest.CONSTRAINT_VENUE_EXCLUSIVE,
                )
                lecturer_id = request.POST.get("lecturer_id")
                lecturer = None
                if constraint_type not in VENUE_TYPES:
                    if not lecturer_id:
                        return JsonResponse({"status": "error", "message": "Select the lecturer this constraint applies to."}, status=400)
                    lecturer = get_object_or_404(Lecturer, pk=lecturer_id)

                # ---- Venue-side constraint types -----------------------------
                if constraint_type == SpecialRequest.CONSTRAINT_VENUE_BLOCK:
                    venue_ids = request.POST.getlist("venue_ids[]")
                    if not venue_ids:
                        return JsonResponse({"status": "error", "message": "Select at least one venue to block."}, status=400)
                    reason = (request.POST.get("reason") or sr.description)[:255]
                    blocks = []
                    for vid in venue_ids:
                        venue = get_object_or_404(Venue, pk=vid)
                        block, _created = VenueBlock.objects.update_or_create(
                            venue=venue,
                            defaults={"reason": reason, "is_active": True},
                        )
                        blocks.append(block)
                    row = blocks[0]
                    row_data = {
                        "venue_ids": [b.venue_id for b in blocks],
                        "venue_codes": [b.venue.code for b in blocks],
                        "reason": reason,
                    }

                elif constraint_type in (SpecialRequest.CONSTRAINT_VENUE_SPECIALIZATION, SpecialRequest.CONSTRAINT_VENUE_EXCLUSIVE):
                    venue_ids = request.POST.getlist("venue_ids[]")
                    if not venue_ids:
                        return JsonResponse({"status": "error", "message": "Select at least one venue to reserve."}, status=400)

                    # Resolve which courses/program this SR covers, so the
                    # reservation targets the right rows in the scheduler.
                    course_qs = ProgramCourse.objects.none()
                    if sr.affected_courses:
                        course_qs = ProgramCourse.objects.filter(course_code__in=sr.affected_courses)
                    elif sr.course_code:
                        course_qs = ProgramCourse.objects.filter(course_code__iexact=sr.course_code)
                    if sr.program_id:
                        course_qs = course_qs.filter(program_id=sr.program_id) if course_qs.exists() else course_qs

                    program_ids = [sr.program_id] if sr.program_id else []
                    course_ids = list(course_qs.values_list("id", flat=True))

                    if not program_ids and not course_ids:
                        return JsonResponse({
                            "status": "error",
                            "message": "This SR has no course or program attached, so a venue reservation can't be created automatically.",
                        }, status=400)

                    exclusive = constraint_type == SpecialRequest.CONSTRAINT_VENUE_EXCLUSIVE
                    if course_ids and program_ids:
                        spec_scope = "mixed"
                    elif course_ids:
                        spec_scope = "course"
                    else:
                        spec_scope = "program"

                    spec = VenueSpecialization.objects.create(
                        name=f"SR #{sr.id} — {sr.department.name}"[:200],
                        notes=(request.POST.get("notes") or sr.description)[:2000] or "",
                        scope=spec_scope,
                        strict=False,
                        exclusive=exclusive,
                    )
                    spec.venues.set(Venue.objects.filter(pk__in=venue_ids))
                    if program_ids:
                        spec.programs.set(Program.objects.filter(pk__in=program_ids))
                    if course_ids:
                        spec.courses.set(course_ids)
                    Venue.objects.filter(pk__in=venue_ids).update(is_specialized=True)

                    row = spec
                    row_data = _serialize_spec(spec)

                elif constraint_type == SpecialRequest.CONSTRAINT_LECTURER_BLOCK:
                    day = request.POST.get("day", "").strip()
                    if not day:
                        return JsonResponse({"status": "error", "message": "Day is required."}, status=400)
                    row = LecturerBlockedSlot.objects.create(
                        lecturer=lecturer,
                        day=day,
                        start_time=_parse_time(request.POST.get("start_time")),
                        end_time=_parse_time(request.POST.get("end_time")),
                        reason=(request.POST.get("reason") or sr.description)[:255],
                    )
                    row_data = _serialize_lecturer_block(row)

                elif constraint_type == SpecialRequest.CONSTRAINT_LECTURER_TIME_PREF:
                    day = request.POST.get("day", "").strip()
                    if not day:
                        return JsonResponse({"status": "error", "message": "Day is required."}, status=400)

                    # Get available timeslots or use whole day
                    start_time = _parse_time(request.POST.get("start_time"))
                    end_time = _parse_time(request.POST.get("end_time"))

                    preference = LecturerTimePreference.objects.create(
                        lecturer=lecturer,
                        notes=(request.POST.get("notes") or sr.description)[:255],
                        is_active=True,
                    )
                    LecturerTimePreferenceSlot.objects.create(
                        preference=preference,
                        day=day,
                        start_time=start_time,
                        end_time=end_time,
                        is_whole_day=(start_time is None and end_time is None),
                    )
                    row = preference
                    row_data = _serialize_time_pref(preference)

                elif constraint_type == SpecialRequest.CONSTRAINT_LECTURER_VENUE_PREF:
                    venue_ids = request.POST.getlist("venue_ids[]")
                    if not venue_ids:
                        return JsonResponse({"status": "error", "message": "Select at least one venue."}, status=400)
                    row = LecturerVenuePreference.objects.create(
                        lecturer=lecturer,
                        notes=(request.POST.get("notes") or sr.description)[:255],
                    )
                    row.venues.set(Venue.objects.filter(pk__in=venue_ids))
                    row_data = _serialize_venue_pref(row)

                else:
                    return JsonResponse({"status": "error", "message": "Invalid constraint type."}, status=400)

                mark_special_request_constraint_applied(sr, constraint_type=constraint_type, constraint_id=row.id)

                return JsonResponse({
                    "status": "success",
                    "constraint_type": constraint_type,
                    "row": row_data,
                    "sr": {
                        "id": sr.id,
                        "status": sr.status,
                        "status_display": sr.get_status_display(),
                        "constraint_applied_type": sr.constraint_applied_type,
                        "constraint_applied_type_display": dict(SpecialRequest.CONSTRAINT_TYPE_CHOICES).get(
                            sr.constraint_applied_type, ""
                        ),
                        "constraint_applied_id": sr.constraint_applied_id,
                    },
                })

        # ------------------------------------------------------------------ #
        #  CONSTRAINT CATEGORY TOGGLES — persisted default on/off per        #
        #  category, shared by both the regular and exam autoschedulers.     #
        # ------------------------------------------------------------------ #
        elif model_type == "constraint_toggle":
            if action == "set":
                ensure_constraint_toggles()
                key = request.POST.get("key", "").strip()
                is_enabled = request.POST.get("is_enabled") == "true"
                toggle = get_object_or_404(SchedulerConstraintToggle, key=key)
                toggle.is_enabled = is_enabled
                toggle.save(update_fields=["is_enabled"])
                return JsonResponse({"status": "success", "key": key, "is_enabled": is_enabled})

        return JsonResponse({"status": "error", "message": "Unknown action or model_type."}, status=400)

    # ---------------------------------------------------------------------- #
    #  GET – render the full panel                                             #
    # ---------------------------------------------------------------------- #

    # ── Buildings and Venues ──────────────────────────────────────────────────
    buildings = Building.objects.select_related("faculty").all().order_by("name")
    venues = Venue.objects.select_related("building").all().order_by("code")
    lab_venues = LabVenue.objects.all().order_by("code")

    # ── Specializations ──────────────────────────────────────────────────────
    specializations = (
        VenueSpecialization.objects
        .prefetch_related("venues", "programs", "courses__program")
        .all()
        .order_by("name")
    )

    # ── Departments, Programs, Courses ──────────────────────────────────────
    departments = Department.objects.select_related("faculty").all().order_by("name")
    programs = Program.objects.select_related("department").all().order_by("name")
    courses = (
        ProgramCourse.objects
        .select_related("program__department")
        .all()
        .order_by("program__name", "year", "course_code")
    )

    # ── Venue Blocks ─────────────────────────────────────────────────────────
    venue_blocks = VenueBlock.objects.select_related("venue").all().order_by("venue__code")

    # ── Resit Exclusions ─────────────────────────────────────────────────────
    resit_venue_exclusions = (
        ResitVenueExclusion.objects
        .select_related("building", "venue", "venue__building")
        .all()
        .order_by("building__name", "venue__code")
    )

    # ── Ensure constraint toggles exist ─────────────────────────────────────
    ensure_constraint_toggles()

    # ── Lecturers ────────────────────────────────────────────────────────────
    lecturers = Lecturer.objects.all().order_by("name")

    # ── Lecturer Blocks (hard rule) ─────────────────────────────────────────
    lecturer_blocks = (
        LecturerBlockedSlot.objects
        .select_related("lecturer")
        .all()
        .order_by("lecturer__name", "day")
    )

    # ── Lecturer Time Preferences (soft rule — updated multi-day) ──────────
    lecturer_time_prefs = (
        LecturerTimePreference.objects
        .select_related("lecturer")
        .prefetch_related("slots")
        .all()
        .order_by("lecturer__name")
    )

    # ── Lecturer Venue Preferences (soft rule) ─────────────────────────────
    lecturer_venue_prefs = (
        LecturerVenuePreference.objects
        .select_related("lecturer")
        .prefetch_related("venues")
        .all()
        .order_by("lecturer__name")
    )

    # ── Constraint Toggles ──────────────────────────────────────────────────
    toggles_by_key = {
        t.key: t for t in SchedulerConstraintToggle.objects.all()
    }
    TAB_BY_KEY = {
        "venue_blocks": "venue_blocks",
        "venue_specialization": "specializations",
        "venue_exclusive": "specializations",
        "lecturer_blocked_slots": "lecturer_blocks",
        "lecturer_time_preference": "lecturer_time_prefs",
        "lecturer_venue_preference": "lecturer_venue_prefs",
    }
    constraint_toggles = [
        {
            "key": d["key"],
            "label": d["label"],
            "description": d["description"],
            "applies_to": d["applies_to"],
            "is_enabled": toggles_by_key[d["key"]].is_enabled if d["key"] in toggles_by_key else True,
            "tab": TAB_BY_KEY.get(d["key"], "buildings"),
        }
        for d in CONSTRAINT_DEFS
    ]

    # ── Special Requests ────────────────────────────────────────────────────
    SR_PANEL_LABELS = SpecialRequest.PANEL_CHOICES
    show_archived_sr = request.GET.get("show_archived_sr") == "1"
    sr_panel = request.GET.get("sr_panel", "all")

    sr_qs_all = list_special_requests_by_department(include_archived=show_archived_sr)

    # Build panel tabs with counts
    sr_panel_tabs = []
    panel_counts = {}
    for sr in sr_qs_all:
        panel_counts[sr.panel] = panel_counts.get(sr.panel, 0) + 1
    sr_panel_tabs.append({"key": "all", "label": "All", "count": len(sr_qs_all)})
    for key, label in SR_PANEL_LABELS:
        sr_panel_tabs.append({"key": key, "label": label, "count": panel_counts.get(key, 0)})

    # Filter by panel
    sr_qs = sr_qs_all if sr_panel == "all" else [sr for sr in sr_qs_all if sr.panel == sr_panel]

    # Group by department
    special_requests_by_department = []
    _dept_bucket = {}
    for sr in sr_qs:
        dept_name = sr.department.name if sr.department_id else "Unassigned"
        if dept_name not in _dept_bucket:
            _dept_bucket[dept_name] = []
            special_requests_by_department.append({
                "department": dept_name,
                "requests": _dept_bucket[dept_name]
            })
        _dept_bucket[dept_name].append(sr)

    # ── Get available timeslots for the lecturer time preference form ──────
    available_timeslots = _get_available_timeslots()

    # ── Render ──────────────────────────────────────────────────────────────
    return render(request, "dashboard/venues_panel.html", {
        # Buildings & Venues
        "buildings": buildings,
        "venues": venues,
        "lab_venues": lab_venues,

        # Specializations
        "specializations": specializations,

        # Departments, Programs, Courses
        "departments": departments,
        "programs": programs,
        "courses": courses,

        # Blocks & Exclusions
        "venue_blocks": venue_blocks,
        "resit_venue_exclusions": resit_venue_exclusions,

        # Lecturers
        "lecturers": lecturers,

        # Lecturer Constraints
        "lecturer_blocks": lecturer_blocks,
        "lecturer_time_prefs": lecturer_time_prefs,
        "lecturer_venue_prefs": lecturer_venue_prefs,

        # Constraint Toggles
        "constraint_toggles": constraint_toggles,

        # Day choices for forms
        "day_choices": LecturerTimePreferenceSlot.DAY_CHOICES,

        # Special Requests
        "special_requests_by_department": special_requests_by_department,
        "show_archived_sr": show_archived_sr,
        "sr_panel_tabs": sr_panel_tabs,
        "sr_panel": sr_panel,

        # Available timeslots for lecturer time preference form
        "available_timeslots": available_timeslots,
    })