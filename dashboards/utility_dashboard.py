from django.shortcuts import render

from core.rbac import allowed_roles, Role
from room_management.models import Venue, Building
from meeting_venues.models import MeetingVenue, MeetingBooking


@allowed_roles(Role.UTILITY, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def utility_dashboard(request):
    """
    Landing page for the Utility Office. From here they choose whether to:
      • Manage (timetabling) venues, buildings, lab venues, specializations,
        blocks, and lecturer constraints — the existing Venues panel.
      • Manage meeting venues and the live booking board (book / unbook /
        transfer meetings) — the new Meeting Venues panel.
    Purely a navigation hub — does not alter either underlying panel.
    """
    context = {
        "venues_count": Venue.objects.count(),
        "buildings_count": Building.objects.count(),
        "meeting_venues_count": MeetingVenue.objects.count(),
        "active_meetings_count": MeetingBooking.objects.filter(is_current=True).count(),
    }
    return render(request, "dashboard/utility_dashboard.html", context)
