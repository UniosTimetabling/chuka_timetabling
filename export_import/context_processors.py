"""
export_import/context_processors.py
════════════════════════════════════
Injects `sync_status_badge` into every template so the small Host/Remote
connection indicator (see templates/base.html) can render on ANY page a
superuser opens — not just the Sync Settings page — without every view
having to remember to pass it in.

Deliberately cheap: one `SyncNode.get_settings()` row fetch (a singleton,
pk=1, already cached at the DB layer via a tiny table) and nothing else.
The actual "is this reachable right now" check is never done here — that
happens client-side via /sync/connection-status/ and /sync/test-connection/
so a slow or unreachable remote can never make an unrelated page slow to
render.
"""


def sync_status_context(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_superuser:
        return {}

    try:
        from core.models import SyncNode
        node = SyncNode.objects.filter(pk=1).only("mode", "is_enabled").first()
    except Exception:  # noqa: BLE001 — a context processor must never break page rendering
        node = None

    if not node or not node.is_enabled:
        return {"sync_status_badge": None}

    return {"sync_status_badge": node}
