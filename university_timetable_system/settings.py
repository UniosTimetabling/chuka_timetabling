"""
Django settings for university_timetable_system project.
Secrets and environment-specific values are loaded from .env
"""

from pathlib import Path
import os

# --------------------------------------------------
# BASE DIRECTORY
# --------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------
# .env LOADER  (no third-party library required)
# --------------------------------------------------
def _load_env():
    env_path = BASE_DIR / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()

def _env(key, default=''):
    return os.environ.get(key, default)

def _env_bool(key, default=False):
    val = os.environ.get(key, str(default)).lower()
    return val in ('true', '1', 'yes')

def _env_list(key, default=''):
    raw = os.environ.get(key, default)
    return [v.strip() for v in raw.split(',') if v.strip()]

# --------------------------------------------------
# SECURITY
# --------------------------------------------------
SECRET_KEY = _env('SECRET_KEY', 'django-insecure-change-me-in-production')
DEBUG = _env_bool('DEBUG', True)
ALLOWED_HOSTS = _env_list('ALLOWED_HOSTS', '127.0.0.1,localhost')

# --------------------------------------------------
# APPLICATION DEFINITION
# --------------------------------------------------
INSTALLED_APPS = [
    'import_export',
    'django_ratelimit',
    'corsheaders',   
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Project apps
    'admins',
    'api',
    'classreps',
    'core',
    'course_allocation',
    'course_management',
    'dashboards',
    'department_management',
    'desktop_sync',            # Electron desktop app: token auth + offline pull/push sync API
    'export_import',
    'faculty_management',
    'feedback',
    'help_system',
    'lecturer_portal',
    'mess',
    'mobile_api',              # Mobile app JSON API (student/lecturer timetable, events, QR share)
    'notifications',
    'program_management',
    'room_management',
    'meeting_venues',
    'timetable',
    'odel_system',
    'campuses_timetable',
    'documentation',
    'resits_timetabling',
    'allocation_reports',
    'special_requests',       # COD "SR" (Special Request) system, shared across cod panels

    'backup_system',          # Backup, Disaster Recovery & Audit/Undo System
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # must be directly after SecurityMiddleware
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'backup_system.middleware.AuditContextMiddleware',  # Audit trail context (user/IP per request)
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.CurrentUserMiddleware',
    'core.middleware.RequestLoggingMiddleware',
    'core.middleware.SecurityHeadersMiddleware',
    'django_ratelimit.middleware.RatelimitMiddleware',
    'core.doc_export.ExportFormatMiddleware',  # PDF/Word export choice (?export_format=docx)
]

# --------------------------------------------------
# URLS & WSGI
# --------------------------------------------------
ROOT_URLCONF = 'university_timetable_system.urls'
WSGI_APPLICATION = 'university_timetable_system.wsgi.application'

# --------------------------------------------------
# TEMPLATES
# --------------------------------------------------
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.rbac.rbac_context',  # injects user_roles & is_management into all templates
            ],
        },
    },
]

# --------------------------------------------------
# DATABASE
# --------------------------------------------------
# The SAME .env file is read in two very different places:
#
#   * on the host (manage.py, mysql CLI, cron jobs)  -> DB_HOST=localhost
#     means "the MySQL installed on this machine"     -> correct
#   * inside a container (web / celery_worker / beat) -> DB_HOST=localhost
#     means "this container itself", where nothing is listening on 3306
#     -> every connection fails with "Can't connect to MySQL server on
#        'localhost' (111 Connection refused)"
#
# So the host is resolved at import time instead of being taken literally.
# Rules, in order:
#   1. Not in a container -> DB_HOST is used exactly as written.
#   2. In a container, DB_HOST is a real hostname/IP -> used as written.
#   3. In a container, DB_HOST is loopback -> rewritten to DOCKER_DB_HOST,
#      which defaults to the compose service name `db`. Set
#      DOCKER_DB_HOST=host.docker.internal to reach a MySQL running on the
#      host machine instead (compose adds the host-gateway mapping).
#
# Nothing here ever invents credentials; it only stops a value that cannot
# possibly work in this context from being used silently.

def _in_container():
    """True when this process is running inside a container."""
    if os.path.exists('/.dockerenv'):
        return True
    try:
        with open('/proc/1/cgroup') as fh:
            blob = fh.read()
        return any(m in blob for m in ('docker', 'containerd', 'kubepods', 'podman'))
    except OSError:
        return False


IN_CONTAINER = _in_container()

_LOOPBACK_HOSTS = {'localhost', '127.0.0.1', '::1', '0.0.0.0', ''}

DB_HOST_CONFIGURED = _env('DB_HOST', 'localhost').strip()
DB_HOST_REWRITTEN = False

if IN_CONTAINER and DB_HOST_CONFIGURED.lower() in _LOOPBACK_HOSTS:
    DB_HOST_EFFECTIVE = _env('DOCKER_DB_HOST', '').strip() or 'db'
    DB_HOST_REWRITTEN = True
else:
    DB_HOST_EFFECTIVE = DB_HOST_CONFIGURED or 'localhost'

DB_USER_EFFECTIVE = _env('DB_USER', 'timetabling_user').strip()

# Optional container-only overrides. Needed when the host MySQL and the
# dockerised MySQL are two different servers with two different accounts —
# one DB_PASSWORD cannot be correct for both. Left unset, the normal
# DB_USER/DB_PASSWORD apply everywhere.
if IN_CONTAINER:
    _docker_user = _env('DOCKER_DB_USER', '').strip()
    if _docker_user:
        DB_USER_EFFECTIVE = _docker_user

# MySQL's root account gets its password from MYSQL_ROOT_PASSWORD
# (DB_ROOT_PASSWORD here), NOT from MYSQL_PASSWORD. If someone configures
# DB_USER=root and leaves DB_PASSWORD empty, fall back to the root password
# rather than attempting a passwordless root login that will be refused.
DB_PASSWORD_EFFECTIVE = _env('DB_PASSWORD', '')
if IN_CONTAINER and _env('DOCKER_DB_PASSWORD', ''):
    DB_PASSWORD_EFFECTIVE = _env('DOCKER_DB_PASSWORD', '')
if DB_USER_EFFECTIVE == 'root' and not DB_PASSWORD_EFFECTIVE:
    DB_PASSWORD_EFFECTIVE = _env('DB_ROOT_PASSWORD', '')

if DB_HOST_REWRITTEN:
    import sys as _sys
    _sys.stderr.write(
        "[settings] DB_HOST={!r} cannot work inside a container; "
        "using {!r} instead. Set DOCKER_DB_HOST to override "
        "(e.g. host.docker.internal for a MySQL on the host).\n".format(
            DB_HOST_CONFIGURED, DB_HOST_EFFECTIVE
        )
    )

DATABASES = {
    'default': {
        'ENGINE': _env('DB_ENGINE', 'django.db.backends.mysql'),
        'NAME': _env('DB_NAME', 'timetabling_db'),
        'USER': DB_USER_EFFECTIVE,
        'PASSWORD': DB_PASSWORD_EFFECTIVE,
        'HOST': DB_HOST_EFFECTIVE,
        'PORT': _env('DB_PORT', '3306'),
        'CONN_MAX_AGE': int(_env('DB_CONN_MAX_AGE', '60')),
        'OPTIONS': {
            'charset': 'utf8mb4',
            # Without this a wrong/unreachable host hangs for the OS default
            # (~2 min) per attempt instead of failing fast and retrying.
            'connect_timeout': int(_env('DB_CONNECT_TIMEOUT', '10')),
        }
    }
}
#To switch to sqlite
# import os

# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.sqlite3',
#         'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
#         'OPTIONS': {
#             'timeout': 20,  # Increase timeout to 20 seconds
#             'init_command': 'PRAGMA journal_mode=WAL;'  # Enable WAL mode
#         }
#     }
# }
# --------------------------------------------------
# PASSWORD VALIDATION
# --------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# --------------------------------------------------
# INTERNATIONALIZATION
# --------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# --------------------------------------------------
# STATIC FILES
# --------------------------------------------------
# Each app owns its static assets under `<app>/static/<app>/...`, which
# Django discovers automatically via the default AppDirectoriesFinder.
# BASE_DIR / 'static' is kept only for the handful of truly site-wide
# assets (site logo, root base.html styling) that are not owned by any
# single app.
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# WhiteNoise — serves static files in production with correct MIME types
# and optional compression/caching headers
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# --------------------------------------------------
# MEDIA FILES
# --------------------------------------------------
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Django's defaults (2.5MB) are sized for ordinary form posts, not for an
# admin uploading the mobile app's .apk (core.models.SiteSettings.mobile_apk),
# which can easily be tens of MB. DATA_UPLOAD_MAX_MEMORY_SIZE gates the
# request outright with a 400 if exceeded; FILE_UPLOAD_MAX_MEMORY_SIZE just
# controls when Django spools a file to a temp file on disk instead of
# holding it in memory, so it's kept much lower to avoid ballooning worker
# memory on a big upload.
# NOTE: nginx's client_max_body_size (docker/nginx/default.conf, currently
# 64M) is a separate limit in front of this one — raise both together, or
# nginx will reject the request with a 413 before Django ever sees it.
DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024   # 100MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024    # 10MB

# --------------------------------------------------
# AUTHENTICATION
# --------------------------------------------------
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# --------------------------------------------------
# DEFAULT PRIMARY KEY
# --------------------------------------------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --------------------------------------------------
# FILE UPLOAD SECURITY
# --------------------------------------------------
FILE_UPLOAD_PERMISSIONS = 0o644
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755

# --------------------------------------------------
# SESSION & COOKIE SECURITY
# --------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 1209600
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False
CSRF_USE_SESSIONS = False

# --------------------------------------------------
# CSRF TRUSTED ORIGINS
# --------------------------------------------------
CSRF_TRUSTED_ORIGINS = _env_list(
    'CSRF_TRUSTED_ORIGINS',
    'https://chukaadinistrationapp.pythonanywhere.com'
)

# --------------------------------------------------
# CORS (for the Expo mobile/web app talking to this API)
# --------------------------------------------------
CORS_ALLOWED_ORIGINS = _env_list(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:8081,http://127.0.0.1:8081'
)

# --------------------------------------------------
# SECURITY HEADERS
# --------------------------------------------------
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# The Docker healthcheck hits gunicorn directly on localhost:8000, bypassing
# nginx, so it never carries X-Forwarded-Proto. Without this exemption,
# SECURE_SSL_REDIRECT sends it a 301 to https://, which gunicorn can't
# serve on a plain HTTP port — the healthcheck then hangs and times out.
SECURE_REDIRECT_EXEMPT = [r'^health/?$']

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
else:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

# --------------------------------------------------
# CACHING
# --------------------------------------------------
# 'default' and 'timetable_cache' were LocMemCache, which is per-process
# memory. That's fine on a single-process dev server, but under gunicorn
# with multiple workers each worker had its own copy — so the exam/regular
# autoscheduler's progress cache (timetable/algorithms/progress_tracking_
# autosheduler.py, keyed on the 'default' cache) would be written by
# whichever worker ran the scheduler thread, but progress-poll requests
# hitting any *other* worker read that worker's untouched default snapshot
# (status "idle", progress 0). That's what produced the flickering modal /
# "no live updates" behaviour, even with redis-server running in the
# background — the redis package in requirements.txt was only ever wired
# up as the Celery broker (CELERY_BROKER_URL below), never as a Django
# cache backend. Switching these two to django_redis.cache.RedisCache
# (requires `pip install django-redis`) gives every worker process the
# same shared, cross-process progress store. Redis DB 1 is used here so it
# doesn't collide with Celery's DB 0.
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': _env('REDIS_CACHE_URL', 'redis://localhost:6379/1'),
        'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
        'TIMEOUT': 300,
    },
    'timetable_cache': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': _env('REDIS_CACHE_URL', 'redis://localhost:6379/1'),
        'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
        'TIMEOUT': 3600,
    },
    'file_cache': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': BASE_DIR / 'django_cache',
        'TIMEOUT': 86400,
        'OPTIONS': {'MAX_ENTRIES': 10000},
    },
    # Ratelimit cache — using LocMemCache so no DB table is required.
    # NOTE: LocMemCache is per-process, meaning under multi-worker gunicorn
    # each worker tracks its own counter (effective limit = rate × workers).
    # This is acceptable for abuse-protection in development and single-worker
    # production. If you add Redis/Memcached later, switch RATELIMIT_USE_CACHE
    # to point at that backend instead for cross-worker enforcement.
    #
    # PREVIOUS DatabaseCache config required running:
    #   python manage.py createcachetable
    # which was missing and caused 500s on /api/notifications/.
    # Switched to LocMemCache to eliminate that dependency.
    'ratelimit_cache': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'ratelimit-counters',
    },
}

# --------------------------------------------------
# API THROTTLING (django-ratelimit)
# --------------------------------------------------
RATELIMIT_USE_CACHE = 'ratelimit_cache'
# Custom view rendered when a rate limit is exceeded (see core/views.py: ratelimited_error)
RATELIMIT_VIEW = 'core.views.ratelimited_error'
# django-ratelimit's system check may flag LocMemCache as not supporting
# atomic increment. Silenced deliberately — for abuse-protection (not billing)
# it is accurate enough.
SILENCED_SYSTEM_CHECKS = [
    'django_ratelimit.E003',
]

# --------------------------------------------------
# EMAIL CONFIGURATION (from .env)
# --------------------------------------------------
EMAIL_BACKEND     = _env('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST        = _env('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT        = int(_env('EMAIL_PORT', '587'))
EMAIL_USE_TLS     = _env_bool('EMAIL_USE_TLS', True)
EMAIL_HOST_USER   = _env('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = _env('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL  = _env('DEFAULT_FROM_EMAIL', f'Timetabling System <{EMAIL_HOST_USER}>')
SUPPORT_EMAIL       = _env('SUPPORT_EMAIL', EMAIL_HOST_USER)

# Password reset link valid for 24 hours
PASSWORD_RESET_TIMEOUT = 86400

# --------------------------------------------------
# TIMETABLE LOADING CONFIG
# --------------------------------------------------
TIMETABLE_LOADING_CONFIG = {
    'ENABLE_PROGRESS_TRACKING': True,
    'POLLING_INTERVAL': 1000,
    'OPTIMIZATION_LEVEL': 'high',
    'CHUNK_SIZE': 50,
    'CACHE_TIMEOUT': 300,
    'ENABLE_PREFETCH': True,
    'BATCH_SIZE': 1000,
}

# --------------------------------------------------
# LOGGING — centralized, replaces scattered print()/console debugging.
#
# Three rotating log files under BASE_DIR/logs/:
#   logs/app.log      — general application activity (INFO and above)
#   logs/security.log — auth events: login success/failure, permission
#                        denials, CSRF failures, rate-limit trips
#   logs/errors.log    — everything ERROR and above: unhandled 500s,
#                        scheduler/timetable-generation failures, exceptions
#
# Loggers used across the codebase (get with logging.getLogger(name)):
#   "django"            — framework-level logs
#   "django.request"     — Django's own 500/404 request-handling errors
#   "security"           — core.views (login/logout), core.rbac (denials)
#   "scheduler"          — timetable/algorithms/* generation failures
#   "app"                — general-purpose app logger for everything else
#
# NOTE: this is independent of the per-run timestamped .txt transcripts
# written by the scheduler algorithms themselves (timetable/algorithms/logs/);
# those remain unchanged. This config only adds aggregated, rotating,
# centrally-monitorable logs on top.
# --------------------------------------------------
LOGS_DIR = BASE_DIR / 'logs'
try:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # Read-only filesystem or permissions issue — fall back to /tmp so the
    # app still boots; fix the volume/permissions in production.
    import tempfile
    LOGS_DIR = Path(tempfile.gettempdir()) / 'university_timetabling_logs'
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {name} {module}:{lineno} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {asctime} {message}',
            'style': '{',
        },
    },
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'app_file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'app.log',
            'maxBytes': 5 * 1024 * 1024,   # 5 MB
            'backupCount': 5,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
        'security_file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'security.log',
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
        'errors_file': {
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'errors.log',
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
        'mail_admins': {
            'level': 'ERROR',
            'filters': ['require_debug_false'],
            'class': 'django.utils.log.AdminEmailHandler',
        },
    },
    'loggers': {
        # Django's own framework logger (warnings, deprecations, etc.)
        'django': {
            'handlers': ['app_file', 'errors_file'],
            'level': 'INFO',
            'propagate': False,
        },
        # Django's request logger — fires on every unhandled 500 and on
        # 4xx raised inside view dispatch. This is the primary "500 errors"
        # monitoring hook.
        'django.request': {
            'handlers': ['errors_file', 'mail_admins'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['security_file', 'errors_file'],
            'level': 'WARNING',
            'propagate': False,
        },
        # Auth events: login success/failure, logout, permission denials,
        # CSRF failures, rate-limit trips. Used by core.views and core.rbac.
        'security': {
            'handlers': ['security_file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        # Timetable/exam/resit/ODEL/lab scheduling engines.
        # Used by every module under timetable/algorithms/.
        'scheduler': {
            'handlers': ['app_file', 'errors_file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        # Catch-all application logger for general app code.
        'app': {
            'handlers': ['app_file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
    # Anything logged via a module-level logger that isn't explicitly listed
    # above (logging.getLogger(__name__) in random modules) still lands here.
    'root': {
        'handlers': ['app_file', 'errors_file'],
        'level': 'WARNING',
    },
}

# --------------------------------------------------
# CUSTOM ERROR VIEWS
# --------------------------------------------------
if not DEBUG:
    CSRF_FAILURE_VIEW = 'core.views.csrf_failure'

# --------------------------------------------------
# MOBILE APP — fallback install link
# Used by the Mobile Analytics dashboard (mobile_api/views_mobile_analytics.py)
# to build a QR code + shareable link for installing the app, whenever no
# .apk has been uploaded via SiteSettings (core.models.SiteSettings.mobile_apk)
# to link/QR directly. Staff can override it at any time from the Mobile
# Analytics page (Edit Link → SiteSettings.mobile_app_share_url); that
# override is also what the mobile app shares (GET /api/mobile/app-link/).
# See mobile_api/app_link.py for the resolution order.
# --------------------------------------------------
MOBILE_APP_STORE_URL = _env(
    'MOBILE_APP_STORE_URL',
    'https://play.google.com/store/apps/details?id=ke.ac.chuka.timetable',
)

# --------------------------------------------------
# CELERY (Background Backup / Sync Processing)
# Workers run independently — the request-response cycle never waits for I/O.
# On PythonAnywhere / bare-metal without Redis, tasks fall back to the
# SyncRunner thread-pool in backup_system/tasks.py automatically.
# --------------------------------------------------
CELERY_BROKER_URL = _env('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = _env('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = _env('TIME_ZONE', 'Africa/Nairobi')

# --------------------------------------------------
# BACKUP SYSTEM — local storage root
# Backups land here by default before any remote upload.
# Override with BACKUP_ROOT in .env for a mounted volume.
# --------------------------------------------------
BACKUP_ROOT = _env('BACKUP_ROOT', str(BASE_DIR / 'backups'))

# --------------------------------------------------
# DISASTER RECOVERY — Replica DB alias
# Only activated when DR_REPLICA_HOST is set in .env.
# The DR module UI in /backup/disaster-recovery/ remains usable without it;
# replica features simply stay dormant until a replica is provisioned.
# --------------------------------------------------
if _env('DR_REPLICA_HOST'):
    DATABASES['replica'] = {
        'ENGINE': _env('DB_ENGINE', 'django.db.backends.mysql'),
        'NAME': _env('DR_REPLICA_DB_NAME', _env('DB_NAME')),
        'USER': _env('DR_REPLICA_DB_USER', _env('DB_USER')),
        'PASSWORD': _env('DR_REPLICA_DB_PASSWORD', _env('DB_PASSWORD')),
        'HOST': _env('DR_REPLICA_HOST'),
        'PORT': _env('DR_REPLICA_PORT', '3306'),
        'OPTIONS': {'charset': 'utf8mb4'},
    }