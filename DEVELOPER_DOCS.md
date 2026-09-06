# Chuka University Timetabling System — Developer Documentation

## Overview

This is the **Chuka University Timetabling & Examinations System** — a Django-based web application for managing academic timetables, exam schedules, course allocations, resits, and related administrative workflows. It supports multi-campus scheduling, ODEL/distance learning, role-based access (via `core/rbac.py`), automated conflict detection, PDF/CSV export, an in-system documentation module, and a chatbot feedback widget.

This document is the **file-level / code-level companion** to [`README.md`](README.md). README.md carries the project overview, feature list, ERDs, architecture diagrams, and deployment instructions. This file goes one level deeper: exact file trees per app, template block names, CSS/JS conventions, the RBAC decorator API, and "how do I add X" recipes.

> **Keeping this file accurate:** this project's file layout changes frequently (new view modules, new algorithm variants, new generated static assets). When in doubt, the source of truth is always the actual filesystem (`find <app> -name "*.py"`) and `settings.py` → `INSTALLED_APPS`, not this document. Re-derive the relevant section below if it's been more than a few weeks since it was last checked against the repo.

---

## Project Structure

```
university_timetabling/
│
├── university_timetable_system/         ← Django project configuration
│   ├── settings.py                      ← All settings (loads from .env)
│   ├── urls.py                          ← Root URL configuration
│   ├── wsgi.py
│   └── asgi.py
│
├── templates/                           ← Global templates directory
│   ├── base.html                        ← ROOT base template (all pages extend this)
│   └── emails/                          ← Email HTML templates
│       ├── email_base.html
│       ├── welcome_new_account.html
│       ├── password_reset_request.html
│       └── admin_password_reset.html
│
├── static/
│   ├── css/                             ← All stylesheets (see CSS Architecture below —
│   │                                       contains both curated app-level files and
│   │                                       auto-extracted per-page files; see note)
│   ├── js/                              ← All JavaScript files (same dual convention)
│   └── img/                             ← Static images
│
├── media/                               ← Runtime-generated / uploaded files (gitignored)
│   ├── notifications/                   ← Generated notification PDFs
│   ├── timetables/published/            ← Published timetable PDFs
│   ├── timetable_logos/                 ← University logo uploads
│   ├── site/logo/                       ← Site logo uploads
│   ├── site/photos/                     ← Site photo uploads
│   └── foods/                           ← Mess/canteen food images
│
├── admins/                              ← Sudo admin panel app
│   ├── forms.py
│   ├── manage_cod.py
│   ├── manage_cot_user.py
│   ├── manage_deans.py
│   ├── manage_timetable_view.py
│   ├── resit_import_admin.py            ← Sudo-side resit bulk import
│   ├── sudo_manage_dvc.py
│   ├── sudo_manage_timetabler.py
│   └── templates/admins/
│       ├── admin.html
│       ├── bulk_reset_passwords.html
│       ├── cod_management.html
│       ├── create_cot_user.html
│       ├── reset_dean_credentials.html
│       ├── resit_import_admin.html
│       ├── sudo_base.html
│       ├── sudo_dashboard.html
│       ├── sudo_homepage.html
│       ├── sudo_manage_accounts.html
│       ├── sudo_manage_dvc.html
│       └── timetable_manage.html
│
├── api/                                 ← REST/AJAX endpoints, no own templates
│   ├── ajax_program_courses.py
│   ├── conflicts_report_apis.py
│   ├── cot_exam_api.py
│   ├── course_codes_api.py
│   ├── exam_timetable_apis.py
│   ├── lab_timetable_api.py
│   ├── lecturer_api.py
│   ├── regular_timetable_apis.py
│   └── shared_venue_group_api.py
│
├── campuses_timetable/                  ← Multi-campus timetabling app
│   ├── models.py                        ← Campus, CampusCourseAllocation, ProgramYearTracker, ...
│   ├── automatic_views.py
│   ├── manual_views.py
│   ├── course_allocation_views.py
│   ├── pdf_views.py
│   ├── tracker_service.py
│   ├── signals.py                       ← `_trigger_tracker` — keeps ProgramYearTracker in sync
│   └── templates/campus/
│       ├── panel.html
│       ├── auto_scheduler.html
│       ├── manual_timetable.html
│       └── tracker_dashboard.html
│
├── classreps/                           ← Class representative portal (session-based auth)
│   ├── classrep_login.py
│   ├── classrep_dashboard.py
│   ├── edit_minimal_entry.py
│   ├── publish_minimal_timetable.py
│   └── templates/
│       ├── classrep_login.html
│       ├── classrep_dashboard.html
│       ├── classrep_edit_entry.html
│       └── classrep_edit_timetable.html
│
├── core/                                ← Auth, RBAC, middleware, site settings
│   ├── rbac.py                          ← ⭐ Single source of truth for roles & permissions
│   ├── group_required.py                ← Legacy decorator shim — delegates to core.rbac.allowed_roles()
│   ├── middleware.py                    ← CurrentUserMiddleware, RequestLoggingMiddleware, SecurityHeadersMiddleware
│   ├── signals.py                       ← DEFAULT_GROUPS seeding, ROLE_PERMISSION_MAP, current-user threadlocal
│   ├── email_utils.py                   ← Password reset / welcome email senders
│   ├── signed_download.py               ← HMAC-signed public download tokens (TimestampSigner)
│   ├── resources.py                     ← django-import-export Resource classes (currently empty/stub)
│   ├── models.py                        ← SiteSettings, SitePhoto, OrgRole, CotUserProfile, ActivityLog
│   ├── views.py                         ← Login, logout, password reset, universal_error handler
│   ├── tests.py                         ← Real unit test suite for core.rbac (alias resolution, decorators, ClassRep)
│   └── templates/core/
│       ├── login.html
│       ├── error.html
│       ├── password_reset_request.html
│       ├── password_reset_confirm.html
│       ├── password_reset_done.html
│       └── password_reset_success.html
│
├── course_allocation/                   ← Course allocation app
│   ├── models.py                        ← CourseAllocation, SelectionGroup, BaseSelection,
│   │                                       LecturerCourseMapping, ProgramEnrollment, LabAllocation,
│   │                                       SubmissionControl, ArchivedCourseAllocation, CombinedCourseGroup
│   ├── auto_allocate_courses.py
│   ├── program_enrollment.py
│   ├── base_selection_views.py
│   ├── lab_allocations.py
│   ├── view_course_allocations.py
│   ├── course_allocations_list.py
│   ├── cod_panel_clear.py
│   ├── detect_user_department.py
│   ├── toggle_submission_to_tt.py
│   ├── toggle_submission_to_dvc.py
│   └── templates/course_allocation/
│       ├── base.html
│       ├── base_selections.html
│       ├── academic_course_allocations.html
│       ├── program_enrollment.html
│       ├── lab_allocations.html
│       ├── lecturer_course_mapping.html
│       ├── auto_allocate_all.html
│       ├── course_allocations.html
│       └── cod_panel_clear.html
│
├── course_management/                   ← COD/department timetable app
│   ├── cod_panel.py
│   ├── department_timetable.py
│   ├── get_course_name.py
│   └── templates/course_management/
│       ├── cod_panel.html
│       └── department_timetable.html
│
├── dashboards/                          ← Public pages + main dashboards
│   ├── user_dashboard.py
│   ├── venues_panel.py
│   ├── timetable_view.py
│   ├── analysis_dashboard.py
│   ├── cot_exam_timetable.py
│   ├── lab_timetable_panel.py
│   ├── lab_exam_timetable_panel.py
│   ├── exam_autoscheduler_home.py
│   ├── autoscheduler_home.py
│   ├── mainportal.py
│   ├── admin_homepage.py
│   ├── published_timetables.py
│   ├── studentportal.py
│   ├── staffportal.py
│   ├── unios_page.py
│   ├── timetable_dashboard_view.py
│   ├── templatetags/dict_filters.py
│   └── templates/dashboard/
│       ├── base.html                    ← Public page base
│       ├── legacy_base.html             ← Standalone fallback shell (not part of the inheritance chain)
│       ├── dashboard_base.html          ← Green sidebar admin base
│       ├── unios_homepage.html          ← University homepage (+ motivational ticker, feedback widget)
│       ├── student_portal.html          ← Student portal (+ feedback widget)
│       ├── staff_portal.html            ← Staff portal (+ feedback widget)
│       ├── view_timetable.html
│       ├── user_dashboard.html
│       ├── venues_panel.html
│       ├── analysis_dashboard.html
│       ├── timetabling_dashboard.html
│       ├── timetable_editor.html
│       ├── lab_timetable.html
│       ├── lab_exam_timetable.html
│       ├── cot_exam_timetable.html
│       ├── published_timetables.html
│       ├── autoscheduler.html
│       ├── exam_autosheduler.html
│       ├── dual_scheduler.html
│       ├── dual_exam_scheduler.html
│       ├── main_portal.html
│       └── admin_homepage.html
│
├── department_management/               ← Dean panel
│   ├── dean_panel.py
│   └── templates/department_management/
│       └── dean_panel.html
│
├── documentation/                       ← In-system documentation
│   ├── models.py                        ← DocumentationCategory, DocumentationPage, DocumentationSection,
│   │                                       CodeExample, InternalLink, UserBookmark, DocumentationTag,
│   │                                       PageComment, DocumentationRating, UserReadingProgress, ...
│   ├── views.py
│   └── templates/documentation/
│       └── single_page.html
│
├── documentation_data_scripts/          ← One-off scripts seeding documentation content
│   └── documentation1.py ... documentation12.py
│
├── export_import/                       ← PDF/CSV export + smart import
│   ├── models.py                        ← TimetablePdfTemplate, PDFDocument
│   ├── official_timetables.py
│   ├── official_exam_timetable.py
│   ├── publish_timetables_pdfs.py
│   ├── smart_import_export.py
│   ├── download_latest_timetable_pdf.py
│   ├── view_and_export_all_course_allocations.py
│   ├── export_course_allocations_csv.py
│   ├── export_exam_csv.py
│   ├── export_exam_pdf.py
│   ├── course_list_pdf.py
│   ├── global_combined_timetable_pdf.py
│   ├── zero_student_report.py
│   ├── signals.py                       ← `on_pdf_document_saved` — post-save housekeeping for PDFDocument
│   └── templates/export/
│       ├── smart_import_export_page.html
│       ├── view_allocations.html
│       ├── official_timetable_pdf.html
│       ├── timetable_main_pdf.html
│       ├── timetable_exam_main_pdf.html
│       ├── course_list_pdf_progress.html
│       └── zero_student_report_pdf.html
│
├── faculty_management/                  ← DVC panel
│   ├── dvc_panel.py
│   └── templates/faculty/
│       └── dvc_panel.html
│
├── feedback/                            ← Feedback system + chatbot
│   ├── models.py                        ← Feedback
│   ├── chatbot_models.py
│   ├── chat_view.py                     ← Bot chat endpoint
│   ├── chatbot_publisher.py
│   ├── feedback_panel.py
│   ├── views.py                         ← Export/status views
│   └── templates/feedback/
│       ├── feedback.html
│       ├── feedback_panel.html
│       └── export_feedback.html
│
├── help_system/                         ← Placeholder app — models/views/urls exist but
│   │                                       help_system.urls is commented out in the root urls.py,
│   │                                       so none of its endpoints are currently reachable.
│   └── (no templates yet)
│
├── lecturer_portal/                     ← Lecturer allocations view
│   ├── lecturer_panel.py
│   ├── department_lecturers_allocations.py
│   └── templates/lecturer/
│       ├── lecturer_panel.html
│       └── lecture_allocator_panel.html
│
├── mess/                                ← Canteen/mess management
│   ├── models.py                        ← Food, FoodImage, FoodOption, Offer
│   └── templates/mess/
│       ├── user_list.html
│       └── admin.html
│
├── notifications/                       ← Notification system
│   ├── models.py                        ← Notification (target_user / target_group), NotificationManager
│   ├── notification_apis.py
│   └── signals.py                       ← `cleanup_old_notifications`
│
├── odel_system/                         ← ODEL/distance learning timetable
│   ├── models.py                        ← ODELCourseAllocation, ODELTimetableConfig, ODELTimetable, ...
│   ├── views_auto.py
│   ├── views_manual.py
│   ├── views_allocation.py
│   ├── pdf_management_views.py
│   ├── scheduler.py
│   └── templates/odel_system/
│       ├── manual_page.html
│       ├── auto_page.html
│       └── allocation_page.html
│
├── program_management/                  ← Program and course catalogue
│   ├── models.py                        ← Program, ProgramCourse, ProgramCode
│   ├── programs_page.py
│   └── templates/program/
│       └── programs.html
│
├── resits_timetabling/                  ← Resit scheduling module (current development focus)
│   ├── models.py                        ← ResitSchedulerConfig, ResitTimeSlot, StudentResitRegistration,
│   │                                       ResitCourseAllocation, ResitTempTimetable, ResitTimetable,
│   │                                       ResitAutoMergedGroup, ResitArchivedTimetable,
│   │                                       ResitArchivedAllocation, ResitSubmissionControl, ResitPublishedPDF
│   ├── cod_panel.py                     ← COD-side resit course allocation panel
│   ├── resit_import.py                  ← COD-side bulk import (pairs with admins/resit_import_admin.py)
│   ├── resit_allocation_manager.py
│   ├── manual_timetabler.py             ← Manual resit timetable placement + conflict detection
│   ├── resit_autosheduler.py            ← Auto-scheduler entry point for resits
│   ├── clear_timetable.py
│   ├── publish.py                       ← Publishes ResitPublishedPDF
│   ├── resit_timetable_pdf.py
│   └── templates/resits_timetabling/
│       ├── cod_panel.html
│       ├── resit_import.html
│       ├── manual_timetabling.html
│       ├── autosheduler_timetabling.html
│       └── resit_allocation_manager.html
│
├── room_management/                     ← Venue/room management
│   ├── models.py                        ← Building, Venue, VenueSpecialization, LabVenue
│   ├── lab_venues.py
│   └── templates/rooms/
│       └── lab_venues.html
│
├── timetable/                           ← Core timetabling engine
│   ├── models.py                        ← LabSchedulerConfig, ExamSchedulerConfig, SchedulerConfig,
│   │                                       Timetable, TempTimetable, ExamTimetable, ExamTempTimetable,
│   │                                       LabTimetable, LabExamTimetable, TimetableArchive,
│   │                                       SharedVenueExamGroup, MergedCourseGroup, MergedCourseGroupTimetable
│   ├── timetable_panel.py               ← Main timetable panel view
│   ├── exam_timetable_panel.py          ← Exam timetable panel
│   ├── timetable_views_crud.py
│   ├── timetable_entry_crud.py
│   ├── delete_timetable_entry.py
│   ├── delete_all_timetables.py
│   ├── delete_all_Exam_timetables.py
│   ├── delete_exam_entry.py
│   ├── delete_merged_group.py
│   ├── add_to_merged.py
│   ├── remove_from_merged.py
│   ├── main_timetable_view.py
│   ├── exam_timetable_view.py
│   ├── publish_timetables.py
│   ├── history_views.py
│   ├── update_scheduler_config.py
│   ├── update_exam_config.py
│   ├── update_exam_scheduler_config.py
│   ├── algorithms/                      ← Scheduling algorithm implementations (one per scheduling domain)
│   │   ├── stable_sheduling_algorithm.py
│   │   ├── regular_timetable_autosheduler_algorithm.py
│   │   ├── regular_timetable_autosheduler_algorithm_v30.py   ← Newer variant, kept alongside the original
│   │   ├── exam_timetable_autosheduler_algorith.py            ← [sic] filename, exam autoscheduler
│   │   ├── dual_campus_scheduler.py
│   │   ├── dual_campus_exam_autosheduler_algorithm.py
│   │   ├── campus_autosheduler_algorithm.py
│   │   ├── odel_autosheduler_algorithm.py
│   │   ├── resit_autosheduler_algorithm.py
│   │   ├── lab_allocation_autosheduler.py
│   │   ├── run_lab_exam_autoscheduler.py
│   │   ├── progress_tracking_autosheduler.py
│   │   └── logs/                        ← Timestamped scheduler run logs (`*_scheduler_YYYYMMDD_HHMMSS.txt`)
│   └── templates/timetable/
│       ├── base.html                    ← Navy panel base
│       ├── legacy_base.html             ← Standalone fallback shell (not part of the inheritance chain)
│       ├── timetable_panel.html
│       ├── autoscheduler_base.html
│       ├── schedule_main_exam.html
│       ├── main_timetable.html
│       ├── view_timetable.html
│       ├── exam_timetable.html
│       ├── config_form.html
│       ├── archive_combined.html
│       ├── official_timetable_pdf.html
│       ├── delete_timetable_confirm.html
│       ├── delete_exam_timetable_confirm.html
│       └── templatetags/custom_filters.py   ← ⚠ misplaced: lives under templates/, not at app root
│           (Django template tag libraries must be importable as `<app>.templatetags.<module>`;
│            confirm this still resolves before relying on it, or move it to `timetable/templatetags/`)
│
├── feasibility_reports/                 ← NOT a Django app — generated diagnostic output written by
│   │                                       the regular/exam autoscheduler algorithms
│   │                                       (`feasibility_report_<timestamp>.html` / `.json`)
│
├── manage.py
├── documentation_seed.py
├── clear_resit_data.py                  ← Standalone maintenance script for resit data
├── export_data.py / import_data.py      ← Standalone bulk data export/import scripts
├── fix_course_load_and_groups.py
├── fix_mapping.py
├── fix_zero_students.py
├── .env                                 ← Local env vars (not committed)
├── .env.example                         ← Template for .env — see security note in README.md
├── .gitignore                           ← Note: also excludes *.json and *.sql project-wide
├── README.md
├── DEVELOPER_DOCS.md
└── RBAC.md
```

> `help_system` is intentionally listed without a URL prefix — its `urls.py` is **not** included in `university_timetable_system/urls.py` (the `include("help_system.urls")` line is commented out), so none of its views are currently reachable from the browser. Treat it as in-progress scaffolding, not a live feature.

---

## Template Inheritance Hierarchy

```
templates/base.html                               ← ROOT (global HTML shell)
│
├── dashboards/templates/dashboard/base.html      ← Public pages
│   ├── unios_homepage.html                       ← University homepage + feedback widget
│   ├── student_portal.html                       ← Student portal + feedback widget
│   ├── staff_portal.html                         ← Staff portal + feedback widget
│   ├── view_timetable.html
│   ├── analysis_dashboard.html
│   └── ...
│
├── dashboards/templates/dashboard/dashboard_base.html  ← Green sidebar admin
│   ├── user_dashboard.html, venues_panel.html, lab_timetable.html, ...
│
├── timetable/templates/timetable/base.html       ← Navy panel pages
│   ├── timetable_panel.html, autoscheduler_base.html, schedule_main_exam.html, ...
│
├── admins/templates/admins/sudo_base.html        ← Sudo admin
│   ├── sudo_dashboard.html, sudo_homepage.html, ...
│
└── core/templates/core/
    ├── login.html                                ← Login (no navbar/footer)
    └── error.html                                ← Error page (403/404/500)
```

`dashboards/templates/dashboard/legacy_base.html` and `timetable/templates/timetable/legacy_base.html` are **not** part of this chain — each is a complete, standalone `<html>` document (its own `<head>`, CDN links, etc.) kept as a fallback for pages that haven't been migrated to the shared base templates yet. Don't `{% extends %}` from them for new pages; use the chain above instead.

---

## Theme & Design System

### Color Tokens (defined in `static/css/base.css :root`)

The actual token system is a two-tier scale: numbered primitives, plus semantic `--cu-*` aliases that map onto them. **New code should reference the `--cu-*` aliases**, not the numbered primitives directly, so the palette can be re-themed in one place.

**Primitives:**

| Variable | Value |
|---|---|
| `--green-900` / `-800` / `-700` / `-600` / `-100` / `-50` | `#002b20` … `#f0faf5` |
| `--blue-900` / `-800` / `-700` / `-600` / `-100` / `-50` | `#0a1f3d` … `#eff6ff` |
| `--gold` / `--gold-light` | `#b38a4a` / `#f4e8d0` |
| `--grey-50` / `-100` / `-200` / `-400` / `-600` / `-800` | `#f8f9f8` … `#252825` |

**Semantic aliases (use these):**

| Variable | Resolves to | Usage |
|---|---|---|
| `--cu-green` | `--green-700` (`#005a48`) | Primary brand color |
| `--cu-green-deep` | `--green-800` (`#004033`) | Darker green (headings, sidebar) |
| `--cu-green-light` | `--green-100` (`#e0f4ed`) | Hover backgrounds, badges |
| `--cu-blue` | `--blue-700` (`#1245a8`) | Staff/alternate accent |
| `--cu-blue-deep` | `--blue-900` | Dark navy accents (panel headers) |
| `--cu-blue-light` | `--blue-100` | Staff hover/badge backgrounds |
| `--cu-gold` | `--gold` (`#b38a4a`) | Highlights, active states |
| `--cu-gold-light` | `--gold-light` | Highlight backgrounds |
| `--cu-bg` | `--grey-50` (`#f8f9f8`) | Page background |
| `--cu-text` | `--grey-600` (`#4a504c`) | Body text |
| `--cu-heading` | `--green-800` (`#004033`) | Headings |
| `--cu-muted` | `--grey-400` | Secondary/muted text |
| `--cu-border` | `--grey-200` (`#dde0dd`) | Borders, dividers |

**Elevation & shape:**

| Variable | Value |
|---|---|
| `--shadow-xs` … `--shadow-xl` | 5-step shadow scale, `rgba(0,0,0,.06)` → `.14` |
| `--shadow-3d` | Compound shadow for raised cards |
| `--r-sm` / `-md` / `-lg` / `-xl` / `-pill` | `6px` / `12px` / `18px` / `24px` / `100px` border-radius scale |
| `--ease` / `--ease-out` | `0.18s ease` / `0.30s cubic-bezier(0.22,1,0.36,1)` |

### Typography

| Variable | Font | Usage |
|---|---|---|
| `--font-display` | `'Source Serif 4', Georgia, serif` | Headings, titles, brand |
| `--font-body` | `'Inter', system-ui, -apple-system, sans-serif` | Body text, UI elements |

### Page Types

| Page Type | Base Template | CSS Files | JS Files |
|---|---|---|---|
| Public website | `dashboard/base.html` | `base.css` + `website.css` | `dashboards_dashboard_base.js` (inline minimal otherwise) |
| Green sidebar admin | `dashboard/dashboard_base.html` | `base.css` + `dashboard.css` + page CSS | `dashboard.js` + page JS |
| Navy panel (timetable) | `timetable/base.html` | `base.css` + `panel.css` + page CSS | `panel.js` + jQuery + page JS |
| Sudo admin | `admins/templates/admins/sudo_base.html` | `base.css` + `admins_sudo_base.css` | `admins_sudo_base.js` |
| Login | `base.html` (direct) | `base.css` + `login.css` | Inline minimal |

---

## CSS / JS File Naming — Two Coexisting Conventions

`static/css/` and `static/js/` currently contain **two overlapping naming conventions** for the same pages, and templates load files from both:

1. **Short/curated names** — hand-named files from earlier in the project, e.g. `cod_panel.css`, `dvc_panel.css`, `course_allocation.css`, `autoscheduler.css`.
2. **Auto-extracted `app_app_template.css` names** — generated when inline `<style>`/`<script>` blocks were pulled out of templates into external files, e.g. `course_allocation_course_allocation_course_allocations.css`, `dashboards_dashboard_lab_timetable.css`. The doubled segment (`course_allocation_course_allocation_...`) comes from `<app>_<template_app_subfolder>_<template_name>`.

**Known issue:** several pages `{% load static %}` both a short-named file *and* its auto-extracted equivalent in the same template (confirmed for `course_allocation`'s templates, likely true elsewhere too). This isn't a typo to "fix" without checking — verify with `grep` on the specific template before assuming one of the two is dead weight:

```bash
grep -rohE "css/[a-zA-Z0-9_./-]+\.css" --include="*.html" <app>/templates/ | sort -u
```

**For new pages:** pick **one** convention and stick to it — prefer a short, hand-named file (`static/css/<app>_<page>.css`) unless you're extracting inline styles from an existing template, in which case the auto-extracted name keeps things traceable back to its origin.

**What goes where:**

| Type | Location | When |
|---|---|---|
| CSS custom properties / tokens | `base.css :root` | New global design variable |
| Layout classes used on 2+ pages | `panel.css` or `dashboard.css` | Reusable component |
| Styles unique to one page | `static/css/[app]_[page].css` | Always external, never inline |
| Short dynamic styles (e.g. progress bar width) | `style=""` attribute | Only when value is Python-driven |

**Never** add `<style>` blocks in templates. Exceptions are very short dynamic properties driven by Python context.

---

## JavaScript Architecture

### Global Helpers

After loading `dashboard.js` or `panel.js`, these are available globally:

```javascript
// CSRF token
window.getCSRFToken()         // dashboard pages
window.getPanelCSRF()         // panel pages

// Secure fetch (auto-attaches CSRF)
window.secureFetch(url, options)   // dashboard
window.panelFetch(url, options)    // panel

// Toasts
window.showToast(message, type, duration)     // dashboard
window.panelToast(message, type, duration)    // panel

// Confirm dialogs
window.showConfirm(msg, onConfirm, label, variant)  // dashboard
window.panelConfirm(msg, onConfirm, label, variant)  // panel

// Loading overlay (panel only)
window.setPanelLoading(show, message)

// Inline field validation error (panel only)
window.setFieldError(field, message)

// Table search
window.attachTableSearch(inputEl, tableEl, cols)

// Modal
window.openPanelModal(id)
window.closePanelModal(id)
```

### AJAX Pattern

```javascript
// POST with CSRF (panel page)
panelFetch('/api/some-endpoint/', {
  method: 'POST',
  body: JSON.stringify({ key: 'value' })
})
.then(function(r){ return r.json(); })
.then(function(data){
  if(data.ok){
    panelToast('Success!', 'success');
  } else {
    panelToast(data.error || 'Error', 'error');
  }
})
.catch(function(){
  panelToast('Network error', 'error');
});
```

---

## Adding a New Page

### Public Page (extends `dashboard/base.html`)

```python
# views.py
def my_new_page(request):
    return render(request, 'myapp/my_new_page.html', {'data': ...})
```

```html
{% extends "dashboard/base.html" %}
{% load static %}

{% block title %}My New Page{% endblock %}

{% block page_css %}
{{ block.super }}
<link rel="stylesheet" href="{% static 'css/myapp_my_page.css' %}">
{% endblock %}

{% block content %}
<div class="container-custom" style="padding: 4rem 0;">
  <h1 class="section-title">My New Page</h1>
</div>
{% endblock %}
```

### Admin/Dashboard Page (extends `dashboard/dashboard_base.html`)

```html
{% extends "dashboard/dashboard_base.html" %}
{% load static %}

{% block title %}Admin Page{% endblock %}

{% block breadcrumb %}
  <a href="{% url 'homepage' %}">Home</a> › <span>Admin Page</span>
{% endblock %}

{% block dash_content %}
  <div class="page-header">
    <h1>Admin Page</h1>
  </div>
{% endblock %}
```

### Timetable Panel Page (extends `timetable/base.html`)

```html
{% extends "timetable/base.html" %}
{% load static %}

{% block title %}Panel Page{% endblock %}

{% block header_title %}
  <i class="bi bi-calendar3"></i> My Panel
{% endblock %}

{% block navbar_extra %}
  <a href="/path1/" class="panel-subnav-tab active">Tab 1</a>
  <a href="/path2/" class="panel-subnav-tab">Tab 2</a>
{% endblock %}

{% block panel_content %}
<div class="panel-content">
  <div class="panel-card">
    <div class="panel-card-header"><h3>Section</h3></div>
    <div class="panel-card-body">
      <!-- content -->
    </div>
  </div>
</div>
{% endblock %}

{% block panel_css %}
<link rel="stylesheet" href="{% static 'css/mypanel.css' %}">
{% endblock %}

{% block panel_js %}
<script src="{% static 'js/mypanel.js' %}"></script>
{% endblock %}
```

> Available blocks per base template (verify against the actual file if a new one might have been added): `dashboard/base.html` → `page_css`, `dashboard_css`, `robots`, `navbar`, `nav_links`, `content`, `quick_bar`, `footer`, `footer_links`, `extra_js`, `dashboard_js`. `dashboard/dashboard_base.html` → `body_class`, `page_css`, `admin_css`, `navbar`, `content`, `sidebar_links`, `breadcrumb`, `topbar_actions`, `dash_content`, `footer`, `extra_js`, `admin_js`. `timetable/base.html` → `body_class`, `page_css`, `panel_css`, `extra_css`, `robots`, `navbar`, `content`, `header_title`, `navbar_extra`, `panel_content`, `content_extra`, `footer`, `extra_js`, `panel_js`.

---

## Running the Project

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and configure environment variables
cp .env.example .env
# See README.md → Local Development for the security note on .env.example's committed values.

# 3. Apply migrations (also seeds RBAC groups via core/signals.py)
python manage.py migrate

# 4. Collect static files (production only)
python manage.py collectstatic

# 5. Run development server
python manage.py runserver

# 6. Seed documentation data (optional)
python documentation_seed.py

# 7. Run the test suite (core.rbac has real coverage; most other apps' tests.py are stubs)
python manage.py test core
```

---

## Environment Variables

See `.env.example` for the full list. Key variables:

```bash
SECRET_KEY=your-very-long-secret-key-here
DEBUG=False
DB_NAME=timetabling_db
DB_USER=timetabling_db
DB_PASSWORD=your-db-password
DB_HOST=localhost
DB_PORT=3306
ALLOWED_HOSTS=yourdomain.com
CSRF_TRUSTED_ORIGINS=https://yourdomain.com
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
```

---

## Django Apps Reference

| App | Purpose | Key URLs |
|---|---|---|
| `core` | Auth, RBAC engine, middleware, site settings, signed downloads | `/login/`, `/logout/`, `/password-reset/` |
| `dashboards` | Public pages, main views, portals, analysis dashboard | `/`, `/portal/`, `/student/portal/`, `/staff/portal/`, `/analysis/` |
| `admins` | Sudo admin panel | `/sudo/`, `/sudo-dashboard/` |
| `timetable` | Core timetabling engine (regular + exam + lab autoschedulers) | `/autoscheduler/...`, `/scheduler/...` |
| `course_allocation` | Course–lecturer–program allocation | `/course-allocations/...`, `/auto-allocate-courses/` |
| `course_management` | COD/department timetable | `/cod/...` |
| `faculty_management` | DVC panel | `/dvc/` |
| `department_management` | Dean panel | `/faculty/` |
| `lecturer_portal` | Lecturer allocations view | `/lecturers/`, `/lecture-allocator/` |
| `program_management` | Program/course catalog | `/programs/` |
| `export_import` | PDF/CSV export + import | `/export/...`, `/smart-data-import-export/` |
| `classreps` | Class representative portal | `/classrep/...` |
| `feedback` | Feedback + chatbot | `/feedback/`, `/api/bot-chat/` |
| `notifications` | Notification system | `/api/notifications/...` |
| `room_management` | Venue/room management | `/lab-venues/` |
| `mess` | Canteen/mess management | `/mess/`, `/mess-admin/` |
| `campuses_timetable` | Multi-campus timetabling | `/panel/`, `/api/...` |
| `odel_system` | ODEL/distance learning | `/odel/...` |
| `documentation` | System documentation | `/documentation/...` |
| `resits_timetabling` | Resit scheduling (current focus area) | `/resits/...` |
| `api` | REST API endpoints | `/api/...` |
| `help_system` | Help system | *(not routed — see note above)* |

---

## RBAC — Quick Reference for Developers

Full design rationale and role catalogue: **[`RBAC.md`](RBAC.md)**. The summary every contributor needs day-to-day:

```python
from core.rbac import allowed_roles, Role, HOD_AND_ABOVE, MANAGEMENT_ROLES

@allowed_roles(Role.COD, Role.COD_ADMIN, Role.SUDO)
def my_view(request):
    ...

# Class Rep views use a separate session-based guard, not allowed_roles():
from core.rbac import classrep_required

@classrep_required
def classrep_dashboard(request):
    ...
```

- Roles are Django `Group`s, normalised through `Role` constants + `ROLE_ALIASES` (so old/inconsistent group-name strings in legacy view code still work).
- `core.group_required.group_required(*names)` is a **backward-compatible shim** — it forwards directly to `allowed_roles()`. ~20+ older call sites still use it; new code should call `allowed_roles()` directly instead.
- `core.rbac.rbac_context` is registered as a template context processor, so every template gets `user_roles` (set) and `is_management` (bool) without per-view boilerplate.
- `core/tests.py` has real unit-test coverage for the alias resolution, `user_has_role`, `get_user_roles`, the `allowed_roles` decorator, and `classrep_required` — run `python manage.py test core` before changing anything in `rbac.py`.

---

## Feedback Widget

### Overview

A floating chat widget appears in the **bottom-left corner** on three public pages:
- `unios_homepage.html`
- `student_portal.html`
- `staff_portal.html`

### How to Enable

Add `data-feedback-widget="true"` to the `<body>` tag and load `feedback-widget.js`:

```html
<body data-feedback-widget="true">
  ...
  <script src="{% static 'js/feedback-widget.js' %}"></script>
```

### Backend Endpoints

| Method | URL | View | Description |
|---|---|---|---|
| `POST` | `/api/bot-chat/` | `feedback.chat_view.bot_chat` | Widget chat messages |
| `POST` | `/api/submit_feedback/` | `feedback.feedback_panel.submit_feedback` | Save feedback to DB |
| `GET` | `/feedback/` | `feedback.feedback_panel.feedback_page` | Full feedback form page |
| `GET` | `/feedback_panel/` | `feedback.feedback_panel.feedback_panel` | Staff view (login required) |
| `GET` | `/feedback/export/` | `feedback.views.export_feedbacks` | CSV export (staff only) |
| `GET` | `/feedback/export/pdf/` | `feedback.views.export_feedbacks_pdf` | PDF export (staff only) |
| `POST` | `/feedback/update-status/` | `feedback.views.update_feedback_status` | Update status (staff only) |

### Widget CSS Classes

```css
#cu-feedback-widget         /* Root container */
.cu-fb-trigger              /* Green bubble button */
.cu-fb-panel                /* Expandable chat panel */
.cu-fb-header               /* Panel header with avatar */
.cu-fb-messages              /* Message list */
.cu-fb-msg.bot               /* Bot message */
.cu-fb-msg.user              /* User message */
.cu-fb-input-area            /* Input row */
.cu-fb-contact-form          /* Optional contact form */
```

---

## Security & Auth

1. All sessions use Django's built-in session framework
2. CSRF protection is enabled globally
3. Password reset flows use time-limited tokens via `core.email_utils`
4. RBAC enforcement is via `@allowed_roles()` (or the legacy `@group_required()` shim) from `core.rbac` — see [RBAC — Quick Reference for Developers](#rbac-quick-reference-for-developers) above
5. Public timetable/PDF downloads use short-lived HMAC tokens from `core.signed_download` (`make_download_token` / `validate_download_token`) rather than exposing raw media paths
6. Class Rep auth is a separate, plain-session mechanism (`request.session['classrep_id']`), guarded by `classrep_required()`, not Django's `auth` app

### Production Checklist

```python
# settings.py / .env
DEBUG=False
SECRET_KEY=<strong-random-key>
ALLOWED_HOSTS=yourdomain.com
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000
```

> The committed `.env.example` in this repo currently contains real-looking secrets (DB password, Gmail app password, ngrok token). Rotate all of them before any production deployment — see the security note in `README.md`.

---

## Motivational Ticker — Developer Notes

### What it is
A full-width animated banner that sits **between the hero access panel and the Responsibilities section** on the public homepage (`/`). It scrolls text from right to left in a seamless infinite loop.

### Files involved

| File | Role |
|---|---|
| `dashboards/templates/dashboard/unios_homepage.html` | Contains the ticker HTML block (search for `id="motivationTicker"`) |
| `static/css/website.css` | Ticker styles appended at end of file (search for `MOTIVATIONAL TICKER`) |
| `static/js/motivation_ticker.js` | All JS: quote bank, time selector, DOM builder, pause toggle |

### Layout behaviour
The ticker sits **outside** `.hero-full--with-bar` so it does not affect the hero height calculation. On desktop the hero section is constrained with `max-height: calc(100vh - 180px)` leaving the access bar and ticker both visible without scrolling.

### Adding / editing quotes
Open `static/js/motivation_ticker.js`. The `QUOTES` object has five keys:

```
morning    → 05:00 – 11:59
midday     → 12:00 – 13:59
afternoon  → 14:00 – 17:59
evening    → 18:00 – 20:59
night      → 21:00 – 04:59
allday     → always shown (shuffled in alongside time-slot quotes)
```

Each item is `{ lang: 'EN'|'FR', quote: '...', author: '...' }`. Add entries to any key. The ticker duplicates the final list for a seamless CSS loop, so any length works.

### Scroll speed
Controlled by `animation: tickerScroll 80s linear infinite` in `website.css`. Reduce the seconds to scroll faster, increase to slow down.

### Pause / resume
The orange `⏸` button at the right edge toggles `animation-play-state`. Hovering over the track also pauses it (via CSS `:hover` on `.motivation-ticker-track`).

### Accessibility
- `role="marquee"` and `aria-label` on the wrapper
- Pause button has `aria-label` that updates on toggle
- `prefers-reduced-motion` check in JS — if active, animation is disabled and only 3 static quotes are shown

---

## Docker — Developer Notes

### Architecture
```
Browser → Nginx (80/443)
              ↓ proxy_pass
         Gunicorn (8000)   ← Django app
              ↓
           MySQL (3306)
```

### Environment override for Docker
When running via `docker compose`, the `web` service overrides `DB_HOST=db` so Django connects to the `db` container by its service name. All other variables come from your `.env` file.

### Startup sequence (`docker-entrypoint.sh`)
1. Polls the database with a Django connection check (up to 30 retries × 1 s)
2. Runs `python manage.py migrate --noinput` (also seeds RBAC's `DEFAULT_GROUPS`)
3. Starts `gunicorn university_timetable_system.wsgi:application`

### Static files in Docker
`collectstatic` runs at **image build time** (in the `Dockerfile`). The output goes to `/app/staticfiles/`. Nginx serves this directory directly at `/static/` — requests never hit Gunicorn for static content.

### Development with Docker (live reload)
For a dev workflow with auto-reload, override the command in `docker-compose.yml`:
```yaml
  web:
    command: python manage.py runserver 0.0.0.0:8000
    volumes:
      - .:/app          # mount source for live editing
```

### Adding a new Python dependency
1. Add it to `requirements.txt`
2. Rebuild: `docker compose up --build web`

### Resetting the database
```bash
docker compose down -v          # removes db_data volume
docker compose up --build       # fresh DB + migrations
```

---

## Maintenance / One-off Scripts (project root)

These live at the repo root, outside any app, and are run manually via `python <script>.py` (not management commands):

| Script | Purpose |
|---|---|
| `export_data.py` / `import_data.py` | Bulk data export/import across apps (distinct from `export_import` app's PDF/CSV exports) |
| `documentation_seed.py` | Seeds the `documentation` app from `documentation_data_scripts/` |
| `clear_resit_data.py` | Wipes resit timetabling data for a clean re-run |
| `fix_course_load_and_groups.py` | One-off data-repair script for course load / merged-group inconsistencies |
| `fix_mapping.py` | One-off data-repair script for lecturer–course mapping inconsistencies |
| `fix_zero_students.py` | One-off data-repair script for zero-student course allocations |

> These are operational/maintenance tools, not part of the request-handling app code. Read each script's header before running it against a production database — several mutate data directly.

---

## Changelog

| Version | Date | Changes |
|---|---|---|
| 3.2.0 | 2026-06-20 | Added `resits_timetabling` app to docs (was already in `INSTALLED_APPS` but undocumented); documented `core.signed_download`, `core.group_required` shim semantics, dual CSS/JS naming convention, `timetable/algorithms/` actual file list (incl. v30 + per-domain variants), `legacy_base.html` fallback templates, root-level maintenance scripts; corrected `secondary_nav` → `navbar_extra` block name; consolidated duplicate Changelog sections |
| 3.1.0 | 2026-03-30 | Motivational ticker (bilingual EN/FR, time-aware); Docker deployment (Dockerfile, docker-compose, Nginx, MySQL config); requirements.txt; .dockerignore; README & DEVELOPER_DOCS updated |
| 3.0.0 | 2026-03-26 | Multi-campus tracker, campuses_timetable app, chatbot publisher, feedback chatbot improvements, ODEL scheduler enhancements, venues panel upgrades |
| 2.0.0 | 2026-03-21 | Major restructure: inline CSS/JS moved to external files; global base.html; feedback widget; security improvements; full documentation |
| 1.x | 2026-03-20 | Original monolithic inline-CSS templates |