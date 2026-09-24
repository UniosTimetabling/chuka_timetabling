"""
desktop_sync/views_sync.py
============================
GET  /api/desktop/timetable/<kind>/            -> pull (full snapshot)
POST /api/desktop/timetable/<kind>/push/       -> push (batch of queued offline edits)

<kind> is one of "regular" / "exam" / "lab" / "lab_exam", mapping onto
timetable.models.{Timetable,ExamTimetable,LabTimetable,LabExamTimetable}.
The "lab" kinds use a different allocation table (course_allocation.LabAllocation,
via ProgramCourse) and a different venue table (room_management.LabVenue);
KIND_CONFIG reconciles both shapes into the one client-side TimetableEntry
shape (see desktop_app/src/shared/types.ts).

Concurrency token
-----------------
`version` on the wire is content_version(): a hash of the row's scheduling
fields, computed on every read. It changes whenever the slot changes, no
matter what wrote it (web panel bulk_create, autoscheduler publish, queryset
.update(), another desktop) — none of which fire model signals. A push is
applied only if the row's current content_version equals the change's
base_version; otherwise NOTHING is written and status="conflict" is returned
with the server's current row.

Pull semantics
--------------
Pull always returns the FULL current dataset for the kind, limited to the
allocation sets the web timetable panels show by default (the "eligible"
gate). The client reconciles deletions by diffing against that snapshot, so
no tombstones are needed. `since_version` is accepted and ignored.

Push validation
---------------
* regular  -> timetable_panel._check_conflicts (the web panel's own engine)
* lab      -> lab_panel_ops.check_lab_slot_conflicts (the web panel's own engine)
* exam     -> DB constraints + group protection only. The web exam panel's
              clash rules are embedded in view code and not reusable; clashes
              are visible afterwards in the web Exam conflicts view.
* Members of Combined / Merged / Shared-venue groups are read-only from the
  desktop (the web panels move those as one unit); edit them on the web.
"""
import hashlib
import json
import logging
import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.gzip import gzip_page
from django.views.decorators.http import require_GET, require_POST

from course_allocation.allocation_scope import TT_SCOPE_DEFAULT, apply_tt_scope
from course_allocation.models import CourseAllocation, LabAllocation
from room_management.models import LabVenue, Venue
from timetable.models import ExamTimetable, LabExamTimetable, LabTimetable, Timetable

from .models import DesktopSyncOp, SyncMeta
from .views_auth import desktop_auth_required

logger = logging.getLogger("app")

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
OPS = {"create", "update", "move", "delete"}
_ELIGIBLE_SCOPE = {"type": "mode", "mode": TT_SCOPE_DEFAULT}


@dataclass
class KindConfig:
    model: type
    alloc_model: type
    venue_model: type
    allocation_field: str        # FK name on the model: "course_allocation" or "lab_allocation"
    venue_field: str             # FK name on the model: "venue" or "lab_venue"
    venue_kind: str              # "hall" or "lab" — echoed to the client
    has_date: bool               # exam-style kinds carry a concrete calendar date
    get_course_code: Callable
    get_course_name: Callable
    get_lecturer_name: Callable
    get_program_name: Callable
    select_related: tuple


def _lecturer_name(alloc):
    lec = getattr(alloc, "lecturer", None)
    return lec.name if lec else "Unassigned"


def _course_alloc_fields(alloc):
    return (alloc.course_code, alloc.course_name, _lecturer_name(alloc), getattr(getattr(alloc, "program", None), "name", "") or "")


def _lab_alloc_fields(alloc):
    # LabAllocation has no course_code/course_name of its own — via ProgramCourse.
    pc = alloc.program_course
    return (pc.course_code, pc.course_name, _lecturer_name(alloc), getattr(getattr(pc, "program", None), "name", "") or "")


_COURSE_SR = ("course_allocation", "course_allocation__lecturer", "course_allocation__program", "venue")
_LAB_SR = ("lab_allocation", "lab_allocation__lecturer", "lab_allocation__program_course",
           "lab_allocation__program_course__program", "lab_venue")


def _cfg(model, alloc_model, venue_model, alloc_field, venue_field, venue_kind, has_date, fields, sr):
    return KindConfig(
        model=model, alloc_model=alloc_model, venue_model=venue_model,
        allocation_field=alloc_field, venue_field=venue_field, venue_kind=venue_kind, has_date=has_date,
        get_course_code=lambda a: fields(a)[0], get_course_name=lambda a: fields(a)[1],
        get_lecturer_name=lambda a: fields(a)[2], get_program_name=lambda a: fields(a)[3],
        select_related=sr,
    )


KIND_CONFIG: dict[str, KindConfig] = {
    "regular": _cfg(Timetable, CourseAllocation, Venue, "course_allocation", "venue", "hall", False, _course_alloc_fields, _COURSE_SR),
    "exam": _cfg(ExamTimetable, CourseAllocation, Venue, "course_allocation", "venue", "hall", True, _course_alloc_fields, _COURSE_SR),
    "lab": _cfg(LabTimetable, LabAllocation, LabVenue, "lab_allocation", "lab_venue", "lab", False, _lab_alloc_fields, _LAB_SR),
    "lab_exam": _cfg(LabExamTimetable, LabAllocation, LabVenue, "lab_allocation", "lab_venue", "lab", True, _lab_alloc_fields, _LAB_SR),
}


# ── helpers ────────────────────────────────────────────────────────────────

class Reject(Exception):
    """A change the server refuses (validation, clash, group lock). Rolls back and reports status=error."""

    def __init__(self, message, obj=None):
        super().__init__(message)
        self.obj = obj


def _hm(t) -> str:
    return t.strftime("%H:%M:%S")


def content_version(cfg: KindConfig, obj) -> int:
    """52-bit (JS/SQLite-safe) hash of the row's scheduling content. See module docstring."""
    parts = [
        getattr(obj, f"{cfg.allocation_field}_id"),
        getattr(obj, f"{cfg.venue_field}_id"),
        obj.day,
        _hm(obj.start_time),
        _hm(obj.end_time),
        obj.date.isoformat() if cfg.has_date and getattr(obj, "date", None) else "",
    ]
    return int(hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:13], 16)


def _serialize(kind: str, cfg: KindConfig, obj, meta: SyncMeta | None = None):
    alloc = getattr(obj, cfg.allocation_field)
    venue = getattr(obj, cfg.venue_field)
    updated_at = meta.updated_at if meta else getattr(obj, "updated_at", None)
    return {
        "id": f"srv-{kind}-{obj.pk}",
        "server_id": obj.pk,
        "kind": kind,
        "course_allocation_id": alloc.pk,
        "course_code": cfg.get_course_code(alloc),
        "course_name": cfg.get_course_name(alloc),
        "lecturer_name": cfg.get_lecturer_name(alloc),
        "program_name": cfg.get_program_name(alloc),
        "venue_id": getattr(obj, f"{cfg.venue_field}_id"),
        "venue_name": venue.code if venue else "",
        "venue_kind": cfg.venue_kind,
        "day": obj.day,
        "date": obj.date.isoformat() if cfg.has_date and getattr(obj, "date", None) else None,
        "start_time": obj.start_time.strftime("%H:%M"),
        "end_time": obj.end_time.strftime("%H:%M"),
        "version": content_version(cfg, obj),
        "updated_at": updated_at.isoformat() if updated_at else None,
        "updated_by": (meta.updated_by or None) if meta else None,
    }


def _scoped_queryset(cfg: KindConfig):
    qs = cfg.model.objects.select_related(*cfg.select_related)
    return apply_tt_scope(qs, scope=_ELIGIBLE_SCOPE, prefix=f"{cfg.allocation_field}__allocation_set")


def _parse_pk(entry_id):
    s = str(entry_id or "")
    if s.isdigit():
        return int(s)
    tail = s.rsplit("-", 1)[-1] if s.startswith("srv-") else ""
    return int(tail) if tail.isdigit() else None


def _norm_day(value) -> str:
    day = str(value or "").strip().capitalize()
    if day not in DAYS:
        raise Reject(f"'{value}' is not a valid day.")
    return day


def _norm_time(value, label):
    t = value if hasattr(value, "hour") else parse_time(str(value or ""))
    if t is None:
        raise Reject(f"Invalid {label} time '{value}'.")
    return t.replace(second=0, microsecond=0)


def _norm_date(value):
    d = value if hasattr(value, "isoformat") and not isinstance(value, str) else parse_date(str(value or ""))
    if d is None:
        raise Reject(f"Invalid date '{value}'.")
    return d


def _resolve_slot(cfg: KindConfig, payload: dict, current=None):
    """Return (day, start, end, date|None) from payload, falling back to `current` row values."""
    day = payload.get("day") if payload.get("day") else (current.day if current else None)
    start = payload.get("start_time") or (current.start_time if current else None)
    end = payload.get("end_time") or (current.end_time if current else None)
    if day is None or start is None or end is None:
        raise Reject("day, start_time and end_time are required.")
    day, start, end = _norm_day(day), _norm_time(start, "start"), _norm_time(end, "end")
    if not start < end:
        raise Reject("Start time must be before end time.")

    date = None
    if cfg.has_date:
        raw = payload.get("date") or (current.date if current else None)
        if not raw:
            raise Reject("Exam entries need a date.")
        date = _norm_date(raw)
        derived = DAYS[date.weekday()]
        if payload.get("date"):
            day = derived                     # the date is authoritative; never store a mismatched day
        elif day != derived:
            raise Reject("Moving an exam to another day needs the target date.")
    return day, start, end, date


def _get_alloc(cfg: KindConfig, alloc_id):
    try:
        return cfg.alloc_model.objects.get(pk=int(alloc_id))
    except (TypeError, ValueError, cfg.alloc_model.DoesNotExist):
        raise Reject("The course allocation for this entry no longer exists.")


def _get_venue(cfg: KindConfig, venue_id):
    try:
        return cfg.venue_model.objects.get(pk=int(venue_id))
    except (TypeError, ValueError, cfg.venue_model.DoesNotExist):
        raise Reject("That venue does not exist (was the entry dropped on an unassigned venue?).")


def _group_lock_reason(kind: str, cfg: KindConfig, alloc, obj=None):
    """Why this entry must be edited on the web instead, or None."""
    if kind in ("regular", "exam"):
        from timetable.timetable_panel import _get_combined_group_for_allocation

        group, members = _get_combined_group_for_allocation(alloc)
        if group and len(members) > 1:
            label = group.display_name() if hasattr(group, "display_name") else str(group)
            return f"it belongs to the combined course group “{label}”, which must move as one unit"
    if obj is not None:
        if kind == "regular" and obj.merged_timetable_groups.exists():
            return "it is part of a merged course group"
        if kind == "exam" and (obj.merged_course_groups.exists() or obj.shared_venue_groups.exists()):
            return "it is part of a merged / shared-venue exam group"
    return None


def _clash_messages(kind: str, cfg: KindConfig, alloc, venue, day, start, end, exclude_pk=None):
    if kind == "regular":
        from timetable.timetable_panel import _check_conflicts

        full = CourseAllocation.objects.select_related(
            "program", "lecturer", "program_course", "selection_group", "specialization_stem", "student_group"
        ).get(pk=alloc.pk)
        # _check_conflicts has no "ignore this row" parameter, so an entry being
        # edited would clash with its own current slot (same lecturer, same
        # course...). Park the row on a placeholder day inside a savepoint,
        # run the web engine, then roll the placeholder back. queryset.update()
        # fires no signals, so nothing is audited or persisted.
        sid = transaction.savepoint() if exclude_pk else None
        try:
            if exclude_pk:
                cfg.model.objects.filter(pk=exclude_pk).update(day="~")
            messages, blocked = _check_conflicts(full, venue.code, day, start, end, force_override=False, request=None)
        finally:
            if sid:
                transaction.savepoint_rollback(sid)
        return messages if blocked else []
    if kind == "lab":
        from timetable.lab_panel_ops import check_lab_slot_conflicts

        return check_lab_slot_conflicts(alloc, venue, day, start, end, exclude_lab_ids=[exclude_pk] if exclude_pk else [])
    return []  # exam / lab_exam: see module docstring


def _reject_on_clash(kind, cfg, alloc, venue, day, start, end, exclude_pk=None, obj=None):
    messages = _clash_messages(kind, cfg, alloc, venue, day, start, end, exclude_pk)
    if messages:
        raise Reject("Clash: " + " ".join(str(m).replace("❌", "").strip() for m in messages[:3]), obj=obj)


def _ok(local_op_id, entry_id, kind, cfg, obj):
    return {"local_op_id": local_op_id, "entry_id": entry_id, "status": "applied",
            "server_entry": _serialize(kind, cfg, obj, SyncMeta.for_instance(obj))}


# ── endpoints ──────────────────────────────────────────────────────────────

@gzip_page
@require_GET
@desktop_auth_required
def pull_timetable(request, kind: str):
    cfg = KIND_CONFIG.get(kind)
    if cfg is None:
        return JsonResponse({"error": "unknown kind"}, status=404)

    ct = ContentType.objects.get_for_model(cfg.model)
    metas = {m.object_id: m for m in SyncMeta.objects.filter(content_type=ct)}
    rows = list(_scoped_queryset(cfg))
    entries = [_serialize(kind, cfg, o, metas.get(o.pk)) for o in rows]

    digest = hashlib.sha1("|".join(f"{e['server_id']}:{e['version']}" for e in sorted(entries, key=lambda e: e["server_id"])).encode())
    return JsonResponse({
        "kind": kind,
        "server_version": int(digest.hexdigest()[:13], 16),   # changes iff anything in this kind changes
        "entries": entries,
        "deleted_ids": [],                                     # kept for wire compatibility; client diffs the snapshot
    })


@csrf_exempt  # safe: desktop_auth_required enforces token auth; no cookie/session auth is involved
@require_POST
@desktop_auth_required
def push_timetable(request, kind: str):
    cfg = KIND_CONFIG.get(kind)
    if cfg is None:
        return JsonResponse({"error": "unknown kind"}, status=404)
    try:
        body = json.loads(request.body or "{}")
        changes = body.get("changes", [])
        if not isinstance(changes, list):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    if random.random() < 0.02:  # opportunistic housekeeping of the idempotency log
        DesktopSyncOp.objects.filter(created_at__lt=timezone.now() - timedelta(days=30)).delete()

    results = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        base = {"local_op_id": change.get("local_op_id"), "entry_id": change.get("entry_id")}
        try:
            with transaction.atomic():
                results.append(_apply_change(kind, cfg, change, request.user))
        except Reject as exc:
            res = {**base, "status": "error", "message": str(exc)}
            if exc.obj is not None:
                res["server_entry"] = _serialize(kind, cfg, exc.obj, SyncMeta.for_instance(exc.obj))
            results.append(res)
        except IntegrityError:
            results.append({**base, "status": "error", "message": "The server rejected this change (it duplicates an existing entry)."})
        except Exception:  # noqa: BLE001 — one bad item must not abort the batch
            logger.exception("desktop_sync push failed | kind=%s | change=%s", kind, base)
            results.append({**base, "status": "error", "message": "Unexpected server error while applying this change."})
    return JsonResponse({"results": results})


def _record_op(local_op_id, kind, op, pk, user):
    DesktopSyncOp.objects.create(local_op_id=local_op_id, kind=kind, op=op, entry_pk=pk, user=user)


def _apply_change(kind: str, cfg: KindConfig, change: dict, user):
    op = change.get("op")
    entry_id = change.get("entry_id")
    local_op_id = str(change.get("local_op_id") or "")[:64]
    payload = change.get("payload") or {}
    base_version = change.get("base_version", 0)
    username = getattr(user, "username", "") or ""

    if op not in OPS:
        raise Reject(f"Unknown operation '{op}'.")
    if not local_op_id:
        raise Reject("Missing local_op_id.")
    if not isinstance(payload, dict):
        raise Reject("Malformed payload.")

    # Idempotent replay: this exact queued change was already applied (response was lost).
    prior = DesktopSyncOp.objects.filter(local_op_id=local_op_id).first()
    if prior:
        obj = cfg.model.objects.select_related(*cfg.select_related).filter(pk=prior.entry_pk).first() if prior.entry_pk else None
        if obj is None:
            return {"local_op_id": local_op_id, "entry_id": entry_id, "status": "applied"}
        return _ok(local_op_id, entry_id, kind, cfg, obj)

    if op == "create":
        alloc = _get_alloc(cfg, payload.get("course_allocation_id"))
        venue = _get_venue(cfg, payload.get("venue_id"))
        day, start, end, date = _resolve_slot(cfg, payload)
        reason = _group_lock_reason(kind, cfg, alloc)
        if reason:
            raise Reject(f"Can't add this from the desktop app: {reason}. Do it on the web timetable panel.")
        if kind in ("lab",) and cfg.model.objects.filter(**{cfg.allocation_field: alloc}).exists():
            raise Reject("This lab is already scheduled — move the existing session instead of adding another.")
        _reject_on_clash(kind, cfg, alloc, venue, day, start, end)

        obj = cfg.model(**{cfg.allocation_field: alloc, cfg.venue_field: venue, "day": day, "start_time": start, "end_time": end})
        if cfg.has_date:
            obj.date = date
        obj.save()
        SyncMeta.bump(obj, updated_by=username)
        _record_op(local_op_id, kind, op, obj.pk, user)
        obj = cfg.model.objects.select_related(*cfg.select_related).get(pk=obj.pk)
        return _ok(local_op_id, entry_id, kind, cfg, obj)

    # update / move / delete all target an existing server row.
    pk = _parse_pk(entry_id)
    if pk is None:
        raise Reject("This entry was never created on the server.")
    obj = cfg.model.objects.select_related(*cfg.select_related).filter(pk=pk).first()
    if obj is None:
        return {"local_op_id": local_op_id, "entry_id": entry_id, "status": "conflict", "server_entry": None,
                "message": "This entry was deleted on the server."}

    if content_version(cfg, obj) != base_version:
        # Someone else's change landed on this row since the client last saw it. Apply nothing.
        return {"local_op_id": local_op_id, "entry_id": entry_id, "status": "conflict",
                "server_entry": _serialize(kind, cfg, obj, SyncMeta.for_instance(obj))}

    alloc = getattr(obj, cfg.allocation_field)
    reason = _group_lock_reason(kind, cfg, alloc, obj)
    if reason:
        raise Reject(f"Can't {'delete' if op == 'delete' else 'move'} this from the desktop app: {reason}. Do it on the web timetable panel.", obj=obj)

    if op == "delete":
        _record_op(local_op_id, kind, op, None, user)
        obj.delete()
        return {"local_op_id": local_op_id, "entry_id": entry_id, "status": "applied"}

    day, start, end, date = _resolve_slot(cfg, payload, current=obj)
    venue = _get_venue(cfg, payload["venue_id"]) if payload.get("venue_id") is not None else getattr(obj, cfg.venue_field)
    changed = (day, start, end, venue.pk, date) != (obj.day, obj.start_time, obj.end_time,
                                                     getattr(obj, f"{cfg.venue_field}_id"), getattr(obj, "date", None) if cfg.has_date else None)
    if changed:
        _reject_on_clash(kind, cfg, alloc, venue, day, start, end, exclude_pk=obj.pk, obj=obj)
        obj.day, obj.start_time, obj.end_time = day, start, end
        setattr(obj, cfg.venue_field, venue)
        if cfg.has_date:
            obj.date = date
        obj.save()
        SyncMeta.bump(obj, updated_by=username)
    _record_op(local_op_id, kind, op, obj.pk, user)
    obj = cfg.model.objects.select_related(*cfg.select_related).get(pk=obj.pk)
    return _ok(local_op_id, entry_id, kind, cfg, obj)
