# Production readiness notes

## What was verified in this pass

- `python manage.py check` runs clean across the **entire project** (all apps,
  models, URL confs, including the new `allocation_reports` app) — only a
  pre-existing, harmless warning about the cache backend
  (`django_ratelimit.W001`), nothing related to this change.
- All new `allocation_reports` Python files pass `py_compile`.
- The hand-written migration (`allocation_reports/migrations/0001_initial.py`)
  was checked against the model field-by-field.
- `reportlab` (used by the new PDF builder) was already pinned in
  `requirements.txt` — no new dependencies added.
- `__pycache__` / `*.pyc` removed from the whole tree before packaging.

## Fixed in this pass

- **`.env` was not in `.gitignore`.** Your real `.env` (live `SECRET_KEY`,
  `DB_PASSWORD`, `EMAIL_HOST_PASSWORD`, `DJANGO_SUPERUSER_PASSWORD`, etc.) was
  sitting in the project unignored — if this was ever `git add`-ed, those
  secrets would be in your history permanently, even after later deleting the
  file. `.gitignore` now excludes `.env` (and any `.env.*` variant) while
  still allowing `.env.example` to be tracked. **If `.env` has ever been
  committed before, rotate `SECRET_KEY`, `DB_PASSWORD`, `EMAIL_HOST_PASSWORD`
  and the Django superuser password now** — removing it from `.gitignore`
  going forward does not erase it from git history; that needs
  `git filter-repo` (or BFG) plus a force-push, then a credential rotation
  regardless.
- `.gitignore` also now covers `__pycache__/`, `*.pyc`, `venv/`, `*.sqlite3`,
  `staticfiles/`, `media/` (with a `.gitkeep` escape hatch), `logs/*.log`,
  `backups/`, coverage/test artifacts, and editor/OS cruft — none of that
  belongs in version control for a Django deployment.

## Flagged, not changed (deployment-specific — needs your decision, not mine)

- **`DEBUG=True` in your live `.env`.** Fine for staging, unsafe for
  production (verbose tracebacks, no `ALLOWED_HOSTS` enforcement bite). Flip
  to `DEBUG=False` before going live and confirm a real `500.html` /
  `404.html` are in place, since Django stops rendering the debug page the
  moment `DEBUG=False`.
- **`SECRET_KEY` default fallback** is `'django-insecure-change-me-in-production'`
  in `settings.py` — this only matters if `.env` is ever missing at deploy
  time (e.g. a container built without it). Consider making the app fail
  loudly instead of falling back, so a missing `.env` can't silently ship
  with a public, guessable key.
- **`ALLOWED_HOSTS`** in your `.env` already looks production-correct
  (`timetable.chuka.ac.ke`, `www.timetable.chuka.ac.ke`, plus local dev
  hosts) — no change needed, just keep it in sync if the domain changes.
- Standard Django hardening worth a checklist pass before launch if not
  already covered elsewhere in your deployment scripts: `SECURE_SSL_REDIRECT`,
  `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS`, and
  `django.middleware.security.SecurityMiddleware`'s
  `SECURE_PROXY_SSL_HEADER` if you're behind nginx/whitenoise as your
  `docker/nginx` folder suggests.

## Allocation Reports feature — carried over from the previous delivery

Everything from the `allocation_reports` app (COD/DVC/Dean/Timetable-dashboard
wiring, background PDF generation, staleness detection, archiving) is
included as-is in this full project zip — see the previous message for the
detailed breakdown, or read `allocation_reports/*.py` docstrings, which
describe the design inline.
