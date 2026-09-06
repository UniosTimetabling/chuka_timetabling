"""
Special Requests (SR) PDF export — lets the director download the SR list
from /venues/ formatted exactly like every other officially published Chuka
University document (same memo header, logo, motto, reference number, and
directorate line as the published timetables), instead of a plain table.
"""
from collections import OrderedDict

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from core.rbac import allowed_roles, Role
from export_import.models import TimetablePdfTemplate
from export_import.official_timetables import get_logo_url
from special_requests.services import list_special_requests_by_department


@allowed_roles(Role.UTILITY, Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)
def export_special_requests_pdf(request):
    """
    GET params:
      department   – optional department name to filter to a single dept
                      (matches the "PDF" link next to each department
                      section on the /venues/ Special Requests tab).
      show_archived – "1" to include archived SRs (default: active only).
    """
    show_archived = request.GET.get("show_archived") == "1"
    department_filter = (request.GET.get("department") or "").strip()

    srs = list_special_requests_by_department(include_archived=show_archived)
    if department_filter:
        srs = [sr for sr in srs if sr.department and sr.department.name == department_filter]

    by_department = OrderedDict()
    for sr in srs:
        dept_name = sr.department.name if sr.department_id else "Unassigned"
        by_department.setdefault(dept_name, []).append(sr)

    template_config = TimetablePdfTemplate.get_template()
    current_date = timezone.now()
    reference_number = template_config.get_reference_number(current_date.strftime("%d-%b-%Y").upper())

    context = {
        "title": "SPECIAL REQUESTS (SR) REPORT" + (f" — {department_filter}" if department_filter else ""),
        "university_name":  template_config.university_name,
        "motto_latin":      template_config.motto_latin,
        "motto_swahili":    template_config.motto_swahili,
        "directorate_name": template_config.directorate_name,
        "telephone":        template_config.telephone,
        "email":            template_config.email,
        "address":          template_config.address,
        "website":          template_config.website,
        "reference_number": reference_number,
        "current_date":     current_date.strftime("%d %B, %Y"),
        "logo_url":         get_logo_url(request, template_config),
        "by_department":    by_department,
        "total":            len(srs),
        "department_filter": department_filter,
        "show_archived":    show_archived,
    }

    html_string = render_to_string("export/special_requests_pdf.html", context, request=request)
    pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()

    response = HttpResponse(pdf_file, content_type="application/pdf")
    filename = "special_requests_report_" + current_date.strftime("%Y%m%d_%H%M") + ".pdf"
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response
