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
DATABASES = {
    'default': {
        'ENGINE': _env('DB_ENGINE', 'django.db.backends.mysql'),
        'NAME': _env('DB_NAME', 'timetabling_db'),
        'USER': _env('DB_USER', 'timetabling_db'),
        'PASSWORD': _env('DB_PASSWORD', ''),
        'HOST': _env('DB_HOST', 'localhost'),
        'PORT': _env('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
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
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'default-cache',
        'TIMEOUT': 300,
        'OPTIONS': {'MAX_ENTRIES': 10000},
    },
    'timetable_cache': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'timetable-progress',
        'TIMEOUT': 3600,
        'OPTIONS': {'MAX_ENTRIES': 5000},
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