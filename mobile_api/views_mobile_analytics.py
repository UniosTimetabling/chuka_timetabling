"""
mobile_api/views_mobile_analytics.py
=====================================
Staff-facing "Mobile Analytics" page — how many distinct devices have the
Chuka Timetable app installed, split by platform, plus a daily
new-install trend for the last 14 days and the most recently seen
devices. Read-only: the numbers come entirely from what devices have
reported themselves via mobile_api.views_install.report_install (see
DeviceInstall) — nothing here writes anything.

    GET /mobile/analytics/   — the page
"""
from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone

from core.models import SiteSettings
from core.rbac import allowed_roles, Role

from .app_link import resolve_app_link
from .models import DeviceInstall

MOBILE_ANALYTICS_STAFF_ROLES = (Role.SUDO, Role.DIRECTOR, Role.TIMETABLE_ADMIN)

TREND_DAYS = 14


@allowed_roles(*MOBILE_ANALYTICS_STAFF_ROLES)
def mobile_analytics_page(request):
    total_installs = DeviceInstall.objects.count()

    platform_labels = dict(DeviceInstall.PLATFORM_CHOICES)
    by_platform = list(
        DeviceInstall.objects.values("platform")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    for row in by_platform:
        row["label"] = platform_labels.get(row["platform"], row["platform"])
    max_platform = max((row["count"] for row in by_platform), default=0) or 1
    for row in by_platform:
        row["pct"] = round(100 * row["count"] / max_platform)

    now = timezone.now()
    since_7 = now - timedelta(days=7)
    since_30 = now - timedelta(days=30)
    new_last_7 = DeviceInstall.objects.filter(first_seen_at__gte=since_7).count()
    new_last_30 = DeviceInstall.objects.filter(first_seen_at__gte=since_30).count()
    active_last_7 = DeviceInstall.objects.filter(last_seen_at__gte=since_7).count()

    # Daily new-install counts for the trend window, oldest first, so the
    # bars below can be drawn with plain CSS heights — no charting library.
    window_start = (now - timedelta(days=TREND_DAYS - 1)).date()
    daily_counts = (
        DeviceInstall.objects.filter(first_seen_at__date__gte=window_start)
        .annotate(day=TruncDate("first_seen_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    counts_by_day = {row["day"]: row["count"] for row in daily_counts}
    trend = []
    for i in range(TREND_DAYS):
        day = window_start + timedelta(days=i)
        trend.append({"day": day, "count": counts_by_day.get(day, 0)})
    max_trend = max((t["count"] for t in trend), default=0) or 1
    for t in trend:
        t["pct"] = round(100 * t["count"] / max_trend)

    recent_installs = DeviceInstall.objects.order_by("-first_seen_at")[:25]

    # "Share the App" card — QR + link. Resolved by the same helper the
    # mobile app's own endpoint uses (see app_link.py), so the two always agree.
    site_settings = SiteSettings.get_settings()
    link = resolve_app_link(request)

    return render(request, "mobile_api/mobile_analytics.html", {
        "total_installs": total_installs,
        "by_platform": by_platform,
        "new_last_7": new_last_7,
        "new_last_30": new_last_30,
        "active_last_7": active_last_7,
        "trend": trend,
        "recent_installs": recent_installs,
        "site_settings": site_settings,
        "app_download_url": link["url"],
        "app_download_source": link["source"],
        "app_download_version": link["version"],
        "app_custom_url": link["custom_url"],
    })
