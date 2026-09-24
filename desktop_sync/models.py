"""
desktop_sync/models.py
=======================
Deliberately does NOT touch timetable.models.Timetable / ExamTimetable /
LabTimetable / LabExamTimetable. Adding columns to those tables would be a
schema migration on live production tables (see MIGRATION_SAFETY.md) — this
app only adds its own side tables:

  * DesktopAuthToken  - one opaque, revocable token per signed-in desktop device
  * SyncMeta          - "last desktop edit" attribution per timetable row
  * DesktopSyncOp     - idempotency log so a retried push (response lost on a
                        flaky network) is never applied twice

The optimistic-concurrency token itself (`version` in the wire format) is NOT
stored here — it is computed from the row's own content on every read
(views_sync.content_version), so it stays correct regardless of whether a
row was last written by the web panel, the autoscheduler, a bulk_create/
queryset.update() (which bypass signals) or the desktop push endpoint.
"""
import binascii
import os

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class SyncMeta(models.Model):
    """Attribution for the last change that arrived through the desktop push endpoint."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    # Informational edit counter (how many desktop pushes touched this row).
    # NOT the concurrency token — see module docstring.
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(default=timezone.now)
    updated_by = models.CharField(max_length=150, blank=True, default="")
    deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = ("content_type", "object_id")
        indexes = [models.Index(fields=["content_type", "version"], name="desktop_syn_content_ver_idx")]

    @classmethod
    def bump(cls, instance, *, updated_by: str = "", deleted: bool = False):
        ct = ContentType.objects.get_for_model(instance.__class__)
        meta, _ = cls.objects.get_or_create(content_type=ct, object_id=instance.pk)
        meta.version += 1
        meta.updated_at = timezone.now()
        meta.updated_by = updated_by or meta.updated_by
        meta.deleted = deleted
        meta.save()
        return meta

    @classmethod
    def for_instance(cls, instance):
        ct = ContentType.objects.get_for_model(instance.__class__)
        return cls.objects.filter(content_type=ct, object_id=instance.pk).first()


def _generate_key() -> str:
    return binascii.hexlify(os.urandom(24)).decode()


class DesktopAuthToken(models.Model):
    """
    Self-contained token auth for the desktop app, deliberately separate
    from any DRF authtoken setup — one row per logged-in desktop device.

    Not a JWT on purpose: a DB-backed opaque token means "log out this
    device" (or an admin revoking a lost laptop's access from the Django
    admin) is a single delete, with no expiry/rotation machinery for v1.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="desktop_tokens")
    key = models.CharField(max_length=48, unique=True, default=_generate_key, editable=False)
    device_label = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["key"], name="desktop_syn_key_idx")]

    def __str__(self):
        return f"{self.user} @ {self.device_label or 'unnamed device'}"

    def touch(self, min_interval_seconds: int = 60):
        """
        Record activity. Uses a queryset .update() (no post_save signal, so it
        doesn't fan out into the project's ActivityLog/AuditLog writers) and
        is throttled, so a burst of sync calls costs one write, not one each.
        """
        now = timezone.now()
        if (now - self.last_used_at).total_seconds() < min_interval_seconds:
            return
        type(self).objects.filter(pk=self.pk).update(last_used_at=now)
        self.last_used_at = now


class DesktopSyncOp(models.Model):
    """
    Idempotency record for pushed changes.

    Every queued desktop change carries a client-generated `local_op_id`. If a
    push is applied but the HTTP response is lost, the client will (correctly)
    resend it. Without this table that would double-create a new row, or turn
    an update into a spurious "conflict with yourself". With it, the server
    recognises the id and simply replays the outcome.
    """

    local_op_id = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=16)
    op = models.CharField(max_length=16)
    entry_pk = models.PositiveIntegerField(null=True, blank=True)  # server row pk (null once deleted)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["created_at"], name="desktop_syn_op_created_idx")]
