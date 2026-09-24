from django.shortcuts import render
from core.models import SiteSettings


def mytimetable(request):
    """Public, self-service 'My Timetable' page.

    Pure shell — all the actual work (resolving a registration number,
    falling back to a department/program picker, downloading the PDF,
    and adding extra courses) happens client-side against the existing
    JSON endpoints:
      - export_import.program_year_pdf_views.resolve_student_scope
      - export_import.program_year_pdf_views.program_timetable_by_ids
      - mobile_api.views_courses (search/add/remove/mine)
    so nothing student-specific ever needs to touch the server side of
    this view itself.
    """
    context = {
        "site_settings": SiteSettings.get_settings(),
    }
    return render(request, "dashboard/my_timetable.html", context)
