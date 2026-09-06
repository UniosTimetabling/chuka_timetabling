from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from core.rbac import allowed_roles, Role

from .models import LabVenue


@allowed_roles(Role.UTILITY, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
@require_http_methods(["GET", "POST"])
def lab_venues(request):
    """
    GET: render the venue management page
    POST (AJAX): handle create / update / detail / delete
    """
    # AJAX POST actions
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        action = request.POST.get("action", "").strip().lower()

        # ---------- Create or Update ----------
        if action in ("create", "update"):
            vid = request.POST.get("id")  # may be empty for create
            code = (request.POST.get("code") or "").strip()
            capacity = request.POST.get("capacity")
            description = request.POST.get("description", "").strip()
            equipment = request.POST.get("equipment", "").strip()

            if not code:
                return JsonResponse({"status": "error", "message": "Venue code is required."}, status=400)

            try:
                capacity_val = int(capacity) if capacity not in (None, "") else None
                if capacity_val is not None and capacity_val < 0:
                    raise ValueError()
            except ValueError:
                return JsonResponse({"status": "error", "message": "Capacity must be a non-negative integer."}, status=400)

            if vid:
                venue = get_object_or_404(LabVenue, pk=vid)
                if LabVenue.objects.exclude(pk=venue.pk).filter(code__iexact=code).exists():
                    return JsonResponse({"status": "error", "message": "Venue code already exists."}, status=400)

                venue.code = code
                venue.capacity = capacity_val
                venue.description = description
                venue.equipment = equipment
                venue.save()
            else:
                if LabVenue.objects.filter(code__iexact=code).exists():
                    return JsonResponse({"status": "error", "message": "Venue code already exists."}, status=400)
                venue = LabVenue.objects.create(
                    code=code,
                    capacity=capacity_val,
                    description=description,
                    equipment=equipment,
                )

            return JsonResponse({
                "status": "success",
                "venue": {
                    "id": venue.id,
                    "code": venue.code,
                    "capacity": venue.capacity,
                    "description": venue.description or "",
                    "equipment": venue.equipment or "",
                }
            })

        # ---------- Detail (for editing) ----------
        if action == "detail":
            vid = request.POST.get("id")
            if not vid:
                return JsonResponse({"status": "error", "message": "Missing id"}, status=400)
            venue = get_object_or_404(LabVenue, pk=vid)
            return JsonResponse({
                "status": "success",
                "venue": {
                    "id": venue.id,
                    "code": venue.code,
                    "capacity": venue.capacity,
                    "description": venue.description or "",
                    "equipment": venue.equipment or "",
                }
            })

        # ---------- Delete ----------
        if action == "delete":
            vid = request.POST.get("id")
            if not vid:
                return JsonResponse({"status": "error", "message": "Missing id"}, status=400)
            venue = get_object_or_404(LabVenue, pk=vid)
            venue.delete()
            return JsonResponse({"status": "success", "id": vid})

        return JsonResponse({"status": "error", "message": "Invalid action"}, status=400)

    # GET -> render template
    venues = LabVenue.objects.all().order_by("code")
    return render(request, "rooms/lab_venues.html", {"venues": venues})
