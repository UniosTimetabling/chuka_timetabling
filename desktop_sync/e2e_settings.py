"""
QA-only settings for testing the desktop app against a THROWAWAY database.

    DJANGO_SETTINGS_MODULE=desktop_sync.e2e_settings python manage.py migrate
    DJANGO_SETTINGS_MODULE=desktop_sync.e2e_settings python manage.py seed_desktop_e2e
    DJANGO_SETTINGS_MODULE=desktop_sync.e2e_settings python manage.py runserver 127.0.0.1:8765

Never point this at real data: it swaps in a local SQLite file and in-process caches, and switches
off the HTTPS redirect so the desktop app can talk plain http to localhost.
"""
import os

from university_timetable_system.settings import *  # noqa: F401,F403
from university_timetable_system.settings import CACHES, SILENCED_SYSTEM_CHECKS

DESKTOP_E2E = True
DEBUG = True
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ.get("DESKTOP_E2E_DB", "/tmp/desktop_e2e.sqlite3")}}
CACHES = {name: {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": name} for name in CACHES}
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
SILENCED_SYSTEM_CHECKS = list(SILENCED_SYSTEM_CHECKS) + ["django_ratelimit.W001"]  # keep the project's own silenced checks
