# ──────────────────────────────────────────────────────────────
# Dockerfile — Chuka University Timetabling System
# Multi-stage: builder (compile deps) + runtime (slim image)
# ──────────────────────────────────────────────────────────────

# ── Stage 1: Builder ──────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System build dependencies (mysqlclient, weasyprint, Pillow)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    default-libmysqlclient-dev \
    pkg-config \
    libcairo2-dev \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    libssl-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=university_timetable_system.settings

# Runtime system libraries only
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libjpeg62-turbo \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy project source
COPY . .

# Create non-root user for security
# Preserve a copy of the repo's shipped media (e.g. site logo) outside
# /app/media, since /app/media gets shadowed at runtime by the named
# media_volume. The entrypoint seeds any missing files from here on
# every container start, without ever overwriting real uploads.
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser \
 && mkdir -p /app/staticfiles /app/media \
 && cp -r /app/media /app/media_defaults \
 && chown -R appuser:appgroup /app /app/media_defaults

USER appuser

# Collect static files at build time, then copy the result outside
# /app/staticfiles into /app/staticfiles_build. /app/staticfiles itself
# gets shadowed at runtime by the named static_volume, just like media
# above — without this copy, every collectstatic run since the volume's
# first-ever creation would be invisible: the volume freezes static
# assets at whatever they were on day one, and nginx/Django keep serving
# that stale snapshot forever regardless of how many times the image is
# rebuilt. The entrypoint fully re-syncs from staticfiles_build into
# staticfiles on every container start (safe to fully overwrite, unlike
# media — these are 100% build artifacts, never user data).
RUN python manage.py collectstatic --noinput --clear \
 && cp -r /app/staticfiles /app/staticfiles_build

EXPOSE 8000

# Entrypoint script handles migrations then starts gunicorn
COPY --chown=appuser:appgroup docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]