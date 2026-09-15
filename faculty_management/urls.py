from django.urls import path
from . import dvc_panel


urlpatterns = [
    # ── DVC Panel ────────────────────────────────────────────────────────────
    path('dvc/', dvc_panel.dvc_panel, name='dvc_panel'),
    path('ajax/disapproved/', dvc_panel.ajax_disapproved_allocations, name='ajax_disapproved'),
    path('ajax/search/faculties/', dvc_panel.ajax_search_faculties, name='ajax_search_faculties'),
    path('ajax/search/departments/', dvc_panel.ajax_search_departments, name='ajax_search_departments'),
    path('ajax/search/lecturers/', dvc_panel.ajax_search_lecturers, name='ajax_search_lecturers'),
]