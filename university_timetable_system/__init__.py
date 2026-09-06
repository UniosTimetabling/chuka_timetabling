# Load Celery app on Django startup so @shared_task decorators bind correctly.
# On environments without a running broker (PythonAnywhere free tier, CI),
# tasks fall back to the SyncRunner thread-pool — no hard dependency on Redis.
try:
    from .celery import app as celery_app
    __all__ = ('celery_app',)
except Exception:
    # Celery not installed or broker unavailable — safe to ignore at import time.
    __all__ = ()
