from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User, Group
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.db import transaction
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from faculty_management.models import Faculty
from department_management.models import Department
from course_allocation.models import CourseAllocation
from timetable.models import Timetable
from .forms import FacultyForm, DepartmentForm, CourseAllocationForm, TimetableForm
from django.template.loader import render_to_string
from .forms import UserForm, GroupForm
from core.models import OrgRole
from core.rbac import link_department_scope, link_faculty_scope
from core.ai_registry import ai_is_configured
from datetime import datetime
import json

# ---------------- Superuser-only decorator ----------------
def sudo_required(view_func):
    """Restrict access to superusers."""
    return login_required(user_passes_test(lambda u: u.is_superuser)(view_func))

# ---------- UTILS ----------
def normalize_name(name: str) -> str:
    """Normalize faculty/department name to lowercase with underscores."""
    return name.strip().replace(" ", "_").lower()

def create_role_user(role, entity_name, with_admin=False):
    """
    Creates a user for a specific role tied to a faculty/department.
    """
    base_name = normalize_name(entity_name)
    role_username = f"{role}_{base_name}"

    # Create main user
    password = f"{role_username}@2025"
    user, created = User.objects.get_or_create(username=role_username)
    if created:
        user.set_password(password)
        user.save()
        OrgRole.objects.create(title=role_username, user=user)

    # Ensure user is in correct group
    group, _ = Group.objects.get_or_create(name=role)
    if group not in user.groups.all():
        user.groups.add(group)
        user.save()

    # Optional admin user
    if with_admin:
        admin_username = f"{role_username}_admin"
        admin_password = f"{admin_username}@2025"
        admin_user, created = User.objects.get_or_create(username=admin_username)
        if created:
            admin_user.set_password(admin_password)
            admin_user.save()
            OrgRole.objects.create(title=admin_username, user=admin_user)

        admin_group, _ = Group.objects.get_or_create(name=f"{role}_admins")
        if admin_group not in admin_user.groups.all():
            admin_user.groups.add(admin_group)
            admin_user.save()

    return user

def handle_user_option(request, key_existing, entity_name, role=None, with_admin=False):
    """
    Decide whether to use an existing user or create a new role-based user.
    """
    user = None
    option = request.POST.get(f"{key_existing}_option")

    if option == "existing":
        uid = request.POST.get(key_existing)
        user = User.objects.filter(pk=uid).first()

    elif option == "new" and role:
        user = create_role_user(role, entity_name, with_admin=with_admin)

    return user

@sudo_required
def sudo_dashboard_view(request):
    """Render the Sudo Dashboard Quick Links page."""
    context = {
        "year": datetime.now().year,
        "ai_configured": ai_is_configured(),
    }
    return render(request, "admins/sudo_homepage.html", context)

# ---------- SUPERUSER DASHBOARD WITH PAGINATION ----------
@sudo_required
def sudo_dashboard(request):
    # Get all users for dropdowns
    users = User.objects.all()[:50]
    
    # Get all data for dropdowns WITH ORDERING to fix pagination warnings
    all_faculties = Faculty.objects.all().order_by('id')
    all_departments = Department.objects.all().order_by('id')
    all_allocations = CourseAllocation.objects.select_related(
        'department', 'origin_department', 'lecturer'
    ).order_by('id')
    all_timetables = Timetable.objects.select_related('course_allocation').order_by('id')
    
    # Get page numbers from request
    faculty_page = request.GET.get('faculty_page', 1)
    dept_page = request.GET.get('dept_page', 1)
    alloc_page = request.GET.get('alloc_page', 1)
    timetable_page = request.GET.get('timetable_page', 1)
    
    # Paginate faculties - load only 5 per page
    faculty_paginator = Paginator(all_faculties, 5)
    try:
        faculties = faculty_paginator.get_page(faculty_page)
    except (PageNotAnInteger, EmptyPage):
        faculties = faculty_paginator.get_page(1)
    
    # Paginate departments - load only 5 per page
    dept_paginator = Paginator(all_departments, 5)
    try:
        departments = dept_paginator.get_page(dept_page)
    except (PageNotAnInteger, EmptyPage):
        departments = dept_paginator.get_page(1)
    
    # Paginate allocations - load only 5 per page
    alloc_paginator = Paginator(all_allocations, 5)
    try:
        allocations = alloc_paginator.get_page(alloc_page)
    except (PageNotAnInteger, EmptyPage):
        allocations = alloc_paginator.get_page(1)
    
    # Paginate timetables - load only 5 per page
    timetable_paginator = Paginator(all_timetables, 5)
    try:
        timetables = timetable_paginator.get_page(timetable_page)
    except (PageNotAnInteger, EmptyPage):
        timetables = timetable_paginator.get_page(1)
    
    context = {
        "faculties": faculties,
        "departments": departments,
        "allocations": allocations,
        "timetables": timetables,
        "faculty_form": FacultyForm(),
        "department_form": DepartmentForm(),
        "allocation_form": CourseAllocationForm(),
        "timetable_form": TimetableForm(),
        "users": users,
        "all_faculties": all_faculties,
        "all_departments": all_departments,
        "all_allocations": all_allocations,
        "all_timetables": all_timetables,
        "faculty_paginator": faculty_paginator,
        "dept_paginator": dept_paginator,
        "alloc_paginator": alloc_paginator,
        "timetable_paginator": timetable_paginator,
        "total_faculties": faculty_paginator.count,
        "total_departments": dept_paginator.count,
        "total_allocations": alloc_paginator.count,
        "total_timetables": timetable_paginator.count,
    }
    return render(request, "admins/sudo_dashboard.html", context)

# ---------- AJAX LOAD MORE DATA ----------
@sudo_required
def ajax_load_more(request):
    """Load more data for a specific table via AJAX - NO PARTIAL TEMPLATES"""
    data_type = request.GET.get('type')
    page = request.GET.get('page', 1)
    
    if data_type == 'faculties':
        queryset = Faculty.objects.all().order_by('id')
        paginator = Paginator(queryset, 5)
        items = paginator.get_page(page)
        
        # Generate HTML directly in Python - NO partial template
        html = ''
        for f in items:
            html += f'''
            <tr data-id="{f.id}">
                <td>{f.id}</td>
                <td class="val-name">{f.name}</td>
                <td class="val-leader">{f.leader if f.leader else "No Leader"}</td>
                <td>
                    <button class="view-btn action-btn" onclick="viewDetails('faculty', {f.id})">View</button>
                    <button class="edit-btn action-btn" onclick="editRow('faculty', {f.id})">Edit</button>
                    <button class="delete-btn action-btn" onclick="deleteItem('faculty', {f.id})">Delete</button>
                </td>
            </tr>
            '''
        
    elif data_type == 'departments':
        queryset = Department.objects.all().order_by('id')
        paginator = Paginator(queryset, 5)
        items = paginator.get_page(page)
        
        # Generate HTML directly in Python
        html = ''
        for d in items:
            html += f'''
            <tr data-id="{d.id}">
                <td>{d.id}</td>
                <td class="val-name">{d.name}</td>
                <td class="val-faculty">{d.faculty.name if d.faculty else "No Faculty"}</td>
                <td class="val-leader">{d.leader if d.leader else "No Leader"}</td>
                <td>
                    <button class="view-btn action-btn" onclick="viewDetails('department', {d.id})">View</button>
                    <button class="edit-btn action-btn" onclick="editRow('department', {d.id})">Edit</button>
                    <button class="delete-btn action-btn" onclick="deleteItem('department', {d.id})">Delete</button>
                </td>
            </tr>
            '''
        
    elif data_type == 'allocations':
        queryset = CourseAllocation.objects.select_related(
            'department', 'origin_department', 'lecturer'
        ).order_by('id')
        paginator = Paginator(queryset, 5)
        items = paginator.get_page(page)
        
        # Generate HTML directly in Python
        html = ''
        for a in items:
            html += f'''
            <tr data-id="{a.id}">
                <td>{a.id}</td>
                <td class="val-code">{a.course_code}</td>
                <td class="val-name">{a.course_name}</td>
                <td class="val-lecturer">{a.lecturer if a.lecturer else "Unassigned"}</td>
                <td class="val-department">{a.department.name if a.department else "No Department"}</td>
                <td class="val-origin">{a.origin_department.name if a.origin_department else "N/A"}</td>
                <td>
                    <button class="view-btn action-btn" onclick="viewDetails('allocation', {a.id})">View</button>
                    <button class="edit-btn action-btn" onclick="editRow('allocation', {a.id})">Edit</button>
                    <button class="delete-btn action-btn" onclick="deleteItem('allocation', {a.id})">Delete</button>
                </td>
            </tr>
            '''
        
    elif data_type == 'timetables':
        queryset = Timetable.objects.select_related('course_allocation').order_by('id')
        paginator = Paginator(queryset, 5)
        items = paginator.get_page(page)
        
        # Generate HTML directly in Python
        html = ''
        for t in items:
            html += f'''
            <tr data-id="{t.id}">
                <td>{t.id}</td>
                <td class="val-course">{t.course_allocation if t.course_allocation else "No Course"}</td>
                <td class="val-venue">{t.venue}</td>
                <td class="val-start">{t.start_time if t.start_time else "N/A"}</td>
                <td class="val-end">{t.end_time if t.end_time else "N/A"}</td>
                <td class="val-day">{t.day}</td>
                <td>
                    <button class="view-btn action-btn" onclick="viewDetails('timetable', {t.id})">View</button>
                    <button class="edit-btn action-btn" onclick="editRow('timetable', {t.id})">Edit</button>
                    <button class="delete-btn action-btn" onclick="deleteItem('timetable', {t.id})">Delete</button>
                </td>
            </tr>
            '''
        
    else:
        return JsonResponse({'success': False, 'error': 'Invalid data type'})
    
    return JsonResponse({
        'success': True,
        'html': html,
        'has_next': items.has_next(),
        'page': page,
        'current_page': items.number,
        'total_pages': paginator.num_pages
    })

# ---------- AJAX MODAL VIEW ----------
@sudo_required
def ajax_modal_view(request):
    """Get detailed view for modal"""
    data_type = request.GET.get('type')
    obj_id = request.GET.get('id')
    
    if data_type == 'faculty':
        obj = get_object_or_404(Faculty, pk=obj_id)
        data = {
            'name': obj.name,
            'leader': str(obj.leader) if obj.leader else 'No Leader',
            'departments_count': obj.department_set.count(),
            'created': obj.created_at.strftime('%Y-%m-%d') if hasattr(obj, 'created_at') else 'N/A',
        }
        
    elif data_type == 'department':
        obj = get_object_or_404(Department, pk=obj_id)
        data = {
            'name': obj.name,
            'faculty': obj.faculty.name,
            'leader': str(obj.leader) if obj.leader else 'No Leader',
            'allocations_count': obj.courseallocation_set.count(),
        }
        
    elif data_type == 'allocation':
        obj = get_object_or_404(CourseAllocation.objects.select_related(
            'department', 'origin_department', 'lecturer'
        ), pk=obj_id)
        data = {
            'course_code': obj.course_code,
            'course_name': obj.course_name,
            'lecturer': str(obj.lecturer) if obj.lecturer else 'Unassigned',
            'department': obj.department.name,
            'origin_department': obj.origin_department.name if obj.origin_department else 'N/A',
            'students': obj.number_of_students,
            'approved': 'Yes' if obj.approved_by_dvc else 'No',
            'rejected': 'Yes' if obj.rejected_by_dvc else 'No',
        }
        
    elif data_type == 'timetable':
        obj = get_object_or_404(Timetable.objects.select_related('course_allocation'), pk=obj_id)
        data = {
            'course': str(obj.course_allocation),
            'venue': obj.venue,
            'start_time': obj.start_time.strftime('%H:%M') if obj.start_time else 'N/A',
            'end_time': obj.end_time.strftime('%H:%M') if obj.end_time else 'N/A',
            'day': obj.day,
        }
        
    else:
        return JsonResponse({'success': False, 'error': 'Invalid data type'})
    
    return JsonResponse({'success': True, 'data': data})

# ---------- FACULTIES ----------
@sudo_required
@transaction.atomic
def ajax_faculty(request):
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add":
            form = FacultyForm(request.POST)
            if form.is_valid():
                obj = form.save(commit=False)
                leader = handle_user_option(
                    request, "leader", obj.name, role="dean", with_admin=True
                )
                obj.leader = leader
                obj.save()
                # `leader` (and, by the "<username>_admin" convention, the
                # Dean Admin account create_role_user() creates alongside
                # it) previously had no way to be resolved back to this
                # faculty — Dean Admin accounts especially had zero
                # association anywhere. Link both now that `obj` has a pk.
                if leader:
                    link_faculty_scope(leader, obj)
                return JsonResponse({"success": True, "id": obj.id, "name": obj.name})
            return JsonResponse({"success": False, "errors": form.errors})

        elif action == "edit":
            obj = get_object_or_404(Faculty, pk=request.POST.get("id"))
            form = FacultyForm(request.POST, instance=obj)
            if form.is_valid():
                obj = form.save()
                return JsonResponse({"success": True, "id": obj.id, "name": obj.name})
            return JsonResponse({"success": False, "errors": form.errors})

        elif action == "delete":
            obj = get_object_or_404(Faculty, pk=request.POST.get("id"))
            obj.delete()
            return JsonResponse({"success": True})

    return JsonResponse({"success": False})

# ---------- DEPARTMENTS ----------
@sudo_required
@transaction.atomic
def ajax_department(request):
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add":
            form = DepartmentForm(request.POST)
            if form.is_valid():
                obj = form.save(commit=False)
                leader = handle_user_option(
                    request, "leader", obj.name, role="cod", with_admin=True
                )
                obj.leader = leader
                obj.save()
                # Same fix as ajax_faculty above, for COD / COD Admin: this
                # is what makes the COD Admin account resolvable to a
                # department, instead of being rejected as
                # "No department associated with your account."
                if leader:
                    link_department_scope(leader, obj)
                return JsonResponse({"success": True, "id": obj.id, "name": obj.name})
            return JsonResponse({"success": False, "errors": form.errors})

        elif action == "edit":
            obj = get_object_or_404(Department, pk=request.POST.get("id"))
            form = DepartmentForm(request.POST, instance=obj)
            if form.is_valid():
                obj = form.save()
                return JsonResponse({"success": True, "id": obj.id, "name": obj.name})
            return JsonResponse({"success": False, "errors": form.errors})

        elif action == "delete":
            obj = get_object_or_404(Department, pk=request.POST.get("id"))
            obj.delete()
            return JsonResponse({"success": True})

    return JsonResponse({"success": False})

# ---------- ALLOCATIONS ----------
@sudo_required
@transaction.atomic
def ajax_allocation(request):
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add":
            form = CourseAllocationForm(request.POST)
            if form.is_valid():
                obj = form.save(commit=False)
                lecturer = handle_user_option(
                    request, "lecturer", obj.department.name, role="lecturer", with_admin=False
                )
                obj.lecturer = lecturer
                obj.save()
                return JsonResponse({"success": True, "id": obj.id, "course": obj.course_name})
            return JsonResponse({"success": False, "errors": form.errors})

        elif action == "edit":
            obj = get_object_or_404(CourseAllocation, pk=request.POST.get("id"))
            form = CourseAllocationForm(request.POST, instance=obj)
            if form.is_valid():
                obj = form.save()
                return JsonResponse({"success": True, "id": obj.id, "course": obj.course_name})
            return JsonResponse({"success": False, "errors": form.errors})

        elif action == "delete":
            obj = get_object_or_404(CourseAllocation, pk=request.POST.get("id"))
            obj.delete()
            return JsonResponse({"success": True})

    return JsonResponse({"success": False})

# ---------- TIMETABLE ----------
@sudo_required
def ajax_timetable(request):
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add":
            form = TimetableForm(request.POST)
            if form.is_valid():
                obj = form.save()
                return JsonResponse({"success": True, "id": obj.id})
            return JsonResponse({"success": False, "errors": form.errors})

        elif action == "edit":
            obj = get_object_or_404(Timetable, pk=request.POST.get("id"))
            form = TimetableForm(request.POST, instance=obj)
            if form.is_valid():
                obj = form.save()
                return JsonResponse({"success": True, "id": obj.id})
            return JsonResponse({"success": False, "errors": form.errors})

        elif action == "delete":
            obj = get_object_or_404(Timetable, pk=request.POST.get("id"))
            obj.delete()
            return JsonResponse({"success": True})

    return JsonResponse({"success": False})

# ---------------- SUDO MANAGE ACCOUNTS ----------------
@sudo_required
def sudo_manage_accounts(request):
    context = {
        "users": User.objects.all(),
        "leaders": OrgRole.objects.select_related("user").all(),
        "groups": Group.objects.all(),
        "user_form": UserForm(),
        "group_form": GroupForm(),
    }
    return render(request, "admins/sudo_manage_accounts.html", context)

def _render_page(request):
    """helper to render full page back"""
    html = render_to_string("admins/sudo_manage_accounts.html", {
        "users": User.objects.all(),
        "leaders": OrgRole.objects.select_related("user").all(),
        "groups": Group.objects.all(),
        "user_form": UserForm(),
        "group_form": GroupForm(),
    }, request=request)
    return JsonResponse({"status": "success", "html": html})

@sudo_required
def ajax_users(request):
    if request.method == "POST":
        if request.POST.get("form_action") in ["add_user", "edit_user"]:
            uid = request.POST.get("user_id")
            inst = get_object_or_404(User, pk=uid) if uid else None
            form = UserForm(request.POST, instance=inst)
            if form.is_valid():
                user = form.save(commit=False)
                pwd = form.cleaned_data.get("password")
                if pwd:
                    user.set_password(pwd)
                user.save()
        elif "delete_user" in request.POST:
            get_object_or_404(User, pk=request.POST.get("user_id")).delete()
        elif "reset_user" in request.POST:
            u = get_object_or_404(User, pk=request.POST.get("user_id"))
            new_pwd = request.POST.get("new_password") or f"{u.username}123"
            u.set_password(new_pwd)
            u.save()
    return _render_page(request)

@sudo_required
def ajax_leaders(request):
    if request.method == "POST":
        if request.POST.get("form_action") == "add_leader":
            uid = request.POST.get("user_id")
            role = request.POST.get("role")
            if uid and role:
                user = get_object_or_404(User, pk=uid)
                OrgRole.objects.create(user=user, title=role)
        elif "delete_leader" in request.POST:
            OrgRole.objects.filter(user_id=request.POST.get("leader_id")).delete()
        elif "reset_leader" in request.POST:
            l = get_object_or_404(User, pk=request.POST.get("leader_id"))
            new_pwd = request.POST.get("new_password") or f"{l.username}123"
            l.set_password(new_pwd)
            l.save()
    return _render_page(request)

@sudo_required
def ajax_groups(request):
    if request.method == "POST":
        if request.POST.get("form_action") in ["add_group","edit_group"]:
            gid = request.POST.get("group_id")
            inst = get_object_or_404(Group, pk=gid) if gid else None
            form = GroupForm(request.POST, instance=inst)
            if form.is_valid():
                form.save()
        elif "delete_group" in request.POST:
            get_object_or_404(Group, pk=request.POST.get("group_id")).delete()
    return _render_page(request)
# ─────────────────────────────────────────────
# BULK PASSWORD RESET (Sudo)
# ─────────────────────────────────────────────
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.urls import reverse
import logging as _logging

from core.email_utils import send_html_email

_logger = _logging.getLogger(__name__)


def _send_reset_link(request, user, temp_password=None):
    """Send a branded HTML password-reset email to a user. Returns (ok, message)."""
    if not user.email:
        return False, f"{user.username} has no email address."

    try:
        uid   = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        url   = request.build_absolute_uri(
            reverse("password_reset_confirm_view", args=[uid, token])
        )
        displayed_pw = temp_password or f"{user.username}@2025"
        user_name    = user.get_full_name() or user.username

        ok, msg = send_html_email(
            subject="Your Account Password Has Been Reset",
            template_name="emails/admin_password_reset.html",
            extra_context={
                "user_name":    user_name,
                "username":     user.username,
                "temp_password": displayed_pw,
                "reset_url":    url,
                "plain_text_fallback": (
                    f"An administrator has reset your account password.\n\n"
                    f"Username: {user.username}\n"
                    f"Temporary Password: {displayed_pw}\n\n"
                    f"Set a new password here: {url}\n"
                    f"This link expires in 24 hours."
                ),
            },
            recipient_list=[user.email],
            request=request,
        )
        if ok:
            return True, f"Email sent to {user.email}"
        _logger.error(f"Reset email failed for {user.username}: {msg}")
        return False, f"Failed to email {user.username}: {msg}"
    except Exception as e:
        _logger.error(f"Reset email failed for {user.username}: {e}")
        return False, f"Failed to email {user.username}: {e}"


@sudo_required
def admin_bulk_reset_password(request):
    """GET: show the page. POST (AJAX): perform reset action."""
    from django.contrib.auth.models import Group

    all_users  = User.objects.filter(is_superuser=False).order_by("username")
    all_groups = Group.objects.all().order_by("name")

    # Annotate groups with user count
    groups_data = []
    for g in all_groups:
        groups_data.append({
            "id": g.id,
            "name": g.name,
            "user_count": g.user_set.filter(is_superuser=False).count(),
        })

    if request.method == "GET":
        ctx = {
            "all_users":      all_users,
            "groups":         groups_data,
            "total_users":    all_users.count(),
            "users_with_email": all_users.exclude(email="").count(),
            "total_groups":   all_groups.count(),
        }
        return render(request, "admins/bulk_reset_passwords.html", ctx)

    # ── AJAX POST ──
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        return JsonResponse({"success": False, "error": "AJAX only."}, status=400)

    action = request.POST.get("action")

    def reset_user_default(user):
        """Set password to username@2025."""
        default_pw = f"{user.username}@2025"
        user.set_password(default_pw)
        user.save()
        return default_pw

    if action == "reset_user":
        uid  = request.POST.get("user_id")
        user = get_object_or_404(User, pk=uid, is_superuser=False)
        reset_user_default(user)
        ok, msg = _send_reset_link(request, user)
        # Even if email fails, password was reset
        return JsonResponse({
            "success": True,
            "message": f"Password reset for {user.username}. " + msg
        })

    elif action == "send_email":
        uid  = request.POST.get("user_id")
        user = get_object_or_404(User, pk=uid, is_superuser=False)
        ok, msg = _send_reset_link(request, user)
        return JsonResponse({"success": ok, "message": msg if ok else None, "error": None if ok else msg})

    elif action == "reset_group":
        from django.contrib.auth.models import Group
        gid   = request.POST.get("group_id")
        group = get_object_or_404(Group, pk=gid)
        users = group.user_set.filter(is_superuser=False)
        count = 0
        email_ok = 0
        for u in users:
            reset_user_default(u)
            ok, _ = _send_reset_link(request, u)
            count += 1
            if ok:
                email_ok += 1
        return JsonResponse({
            "success": True,
            "message": (
                f"Reset {count} user(s) in '{group.name}'. "
                f"Email sent to {email_ok}/{count} with registered emails."
            )
        })

    elif action == "reset_all":
        users = User.objects.filter(is_superuser=False)
        count = 0
        email_ok = 0
        for u in users:
            reset_user_default(u)
            ok, _ = _send_reset_link(request, u)
            count += 1
            if ok:
                email_ok += 1
        return JsonResponse({
            "success": True,
            "message": (
                f"Reset {count} account(s). "
                f"Email sent to {email_ok}/{count} with registered emails."
            )
        })

    return JsonResponse({"success": False, "error": "Unknown action."})