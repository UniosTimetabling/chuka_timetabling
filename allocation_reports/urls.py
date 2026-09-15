from django.urls import path

from . import views

urlpatterns = [
    # COD — own department, isolated
    path("allocations/cod/", views.cod_allocation_panel, name="cod_allocations_panel"),
    path("allocations/cod/all/", views.cod_all_departments_view, name="cod_all_departments_allocations"),

    # DVC / Dean / Timetable dashboard — shared view, distinguished only by
    # which sidebar link points to it; @allowed_roles enforces who may open it.
    path("allocations/dvc/", views.management_dashboard, name="dvc_allocations_dashboard"),
    path("allocations/dean/", views.management_dashboard, name="dean_allocations_dashboard"),
    path("allocations/timetable-dashboard/", views.management_dashboard, name="management_allocations_dashboard"),

    path("allocations/<str:scope>/<int:department_id>/regenerate/", views.force_regenerate, name="allocation_force_regenerate"),
    path("allocations/resit/generate/", views.generate_resit_allocation, name="generate_resit_allocation"),

    path("allocations/api/status/", views.api_status, name="allocation_api_status"),
]
