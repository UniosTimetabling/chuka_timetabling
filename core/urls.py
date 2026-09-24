from django.urls import path
from . import views
from .latest_download_redirect import latest_download_redirect

urlpatterns = [
    path("login/",  views.login_view,  name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("error/",  views.universal_error, name="universal_error"),

    # Public quick-download links (unios homepage, etc.) — resolves the
    # latest published PDF for a section and redirects to a properly
    # signed download URL. Fixes "Invalid or expired download link."
    # showing on links that never carried a token in the first place.
    path("go/<str:section>/", latest_download_redirect, name="latest_download_redirect"),

    # Password reset flow
    path("password-reset/",
         views.password_reset_request,
         name="password_reset_request"),
    path("password-reset/sent/",
         views.password_reset_done_view,
         name="password_reset_done_view"),
    path("password-reset/confirm/<uidb64>/<token>/",
         views.password_reset_confirm_view,
         name="password_reset_confirm_view"),
    path("password-reset/success/",
         views.password_reset_success_view,
         name="password_reset_success_view"),

    # Reusable autoscheduler constraint-confirmation endpoint (regular +
    # future exam/resit autoschedulers all read the same registry).
    path("scheduler/constraints/summary/",
         views.scheduler_constraint_summary,
         name="scheduler_constraint_summary"),
]
