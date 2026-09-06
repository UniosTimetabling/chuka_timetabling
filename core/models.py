# core/models.py
# ===================================================================
#  CORE SYSTEM MODELS
#  Contains site settings, AI providers, user profiles, roles,
#  activity logs, and scheduler constraint toggles.
#  Lecturer preference models are NOT in core — they belong in
#  timetable/models.py where they are used.
# ===================================================================

from django.db import models
from django.contrib.auth.models import User, Group
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.utils import timezone


class SiteSettings(models.Model):
    """
    Singleton model — only one row should ever exist.
    Stores the university name, tagline, logo, and contact info
    that are rendered site-wide through base.html.
    """
    university_name = models.CharField(
        max_length=200,
        default="Chuka University",
        help_text="Full university name shown in the navbar and page titles.",
    )
    tagline = models.CharField(
        max_length=300,
        blank=True,
        default="Excellence in Learning",
        help_text="Short motto or tagline shown in the hero section.",
    )
    logo = models.ImageField(
        upload_to="site/logo/",
        blank=True,
        null=True,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "svg", "webp"])],
        help_text="University crest/logo. Recommended: transparent PNG, min 200×200 px.",
    )
    contact_email = models.EmailField(
        blank=True,
        default="info@chuka.ac.ke",
    )
    contact_phone = models.CharField(max_length=60, blank=True)
    contact_location = models.CharField(
        max_length=200,
        blank=True,
        default="Science Complex (S102), Main Campus",
    )
    website_url = models.URLField(blank=True, default="https://www.chuka.ac.ke")

    # Social links (optional)
    twitter_url = models.URLField(blank=True)
    facebook_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return f"Site Settings — {self.university_name}"

    def save(self, *args, **kwargs):
        # Enforce singleton: always use pk=1
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        """Return the single settings row, creating defaults if missing."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SitePhoto(models.Model):
    """
    Gallery photos shown in the homepage carousel and gallery section.
    The `order` field controls display sequence (lower = first).
    """
    image = models.ImageField(
        upload_to="site/photos/",
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "webp"])],
    )
    caption = models.CharField(max_length=200, blank=True, help_text="Optional caption shown on the carousel slide.")
    alt_text = models.CharField(
        max_length=200,
        blank=True,
        help_text="Screen-reader description of the image.",
    )
    order = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text="Lower numbers appear first in the carousel.",
    )
    is_active = models.BooleanField(default=True, help_text="Uncheck to hide without deleting.")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "uploaded_at"]
        verbose_name = "Site Photo"
        verbose_name_plural = "Site Photos"

    def __str__(self):
        return self.caption or f"Photo #{self.pk} (order {self.order})"


class OrgRole(models.Model):
    """
    Maps each organizational role (dean, cod, dvc, etc.) to a specific User.
    Example: dean -> user(dean)

    NOTE on `title`: this is a human-readable label, NOT a unique key. It used
    to be `unique=True`, which meant only ONE "COD" (or "Dean", or "Dean Admin")
    could ever exist system-wide — creating a second Head of Department or a
    second Dean silently stole the role away from the first one instead of
    failing loudly. The real uniqueness constraint is `user` (OneToOne below):
    each user has at most one OrgRole, which is what actually matters.

    `department` / `faculty` let department- and faculty-scoped admin
    accounts (COD, COD Admin, Dean, Dean Admin) be resolved back to the org
    unit they administer, even when they aren't the literal
    Department.leader / Faculty.leader (e.g. "*_admin" helper accounts).
    See core.rbac.link_department_scope / link_faculty_scope.
    """
    title = models.CharField(max_length=120)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="org_role")
    department = models.ForeignKey(
        'department_management.Department',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="org_roles",
        help_text="Department this role administers (COD / COD Admin).",
    )
    faculty = models.ForeignKey(
        'faculty_management.Faculty',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="org_roles",
        help_text="Faculty this role administers (Dean / Dean Admin).",
    )

    def __str__(self):
        scope = ""
        if self.department_id:
            scope = f" [{self.department}]"
        elif self.faculty_id:
            scope = f" [{self.faculty}]"
        return f"{self.title} -> {self.user.username}{scope}"


class CotUserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="cot_profile")
    department = models.ForeignKey('department_management.Department', on_delete=models.CASCADE, related_name="cot_users")

    def __str__(self):
        return f"COT: {self.user.username} ({self.department.name})"


class AIProviderSettings(models.Model):
    """
    Central, hot-switchable registry of external AI providers
    (Gemini, OpenAI, Anthropic, Ollama, or any OpenAI-compatible custom
    endpoint). Any module in the project (Smart Importer, future
    features, etc.) calls `core.ai_registry.get_ai_client()` instead of
    hard-coding a provider, so swapping or disabling the active AI is a
    one-row DB change with no code edits.

    Multiple rows may exist (one per configured provider) but only one
    may be marked `is_active` at a time — see `save()`.

    SECURITY:
    - `api_key` is never stored in plaintext. It is encrypted at rest
      using Fernet, keyed off Django's SECRET_KEY (see core/ai_registry.py
      `_get_fernet()`), and only decrypted in-memory at call time.
    - The key is never logged, never sent to the browser/template layer,
      and never included in admin list views (write-only widget).
    - If no provider is active/configured, AI-dependent features must
      degrade gracefully rather than error out.
    """

    PROVIDER_CHOICES = [
        ("gemini", "Google Gemini"),
        ("openai", "OpenAI"),
        ("anthropic", "Anthropic (Claude)"),
        ("ollama", "Ollama (local / self-hosted)"),
        ("custom", "OpenAI-compatible endpoint (DeepSeek, BlackBox, Groq, "
                    "OpenRouter, Mistral, Together, xAI/Grok, Fireworks, "
                    "Perplexity, LM Studio, vLLM, etc.)"),
        ("generic", "Generic / fully custom API (any provider, any shape — no code changes)"),
    ]

    # Convenience presets for the "custom" (OpenAI-compatible) provider type.
    # Picking one of these in the admin just pre-fills `base_url` for you —
    # it is NOT required. Any OpenAI-compatible provider works with "custom"
    # even if it isn't listed here; just paste its base URL by hand.
    # Adding a new name to this dict is a one-line, no-migration change.
    OPENAI_COMPATIBLE_PRESETS = {
        "openai": "https://api.openai.com/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "blackbox": "https://api.blackbox.ai/v1",
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "mistral": "https://api.mistral.ai/v1",
        "together": "https://api.together.xyz/v1",
        "fireworks": "https://api.fireworks.ai/inference/v1",
        "xai": "https://api.x.ai/v1",
        "perplexity": "https://api.perplexity.ai",
    }

    name = models.CharField(
        max_length=80,
        help_text="Friendly label, e.g. 'Gemini Flash (default)' or 'Office Ollama'.",
    )
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    model_name = models.CharField(
        max_length=120,
        help_text="Model identifier the provider expects, e.g. 'gemini-2.5-flash', "
                   "'gpt-4o-mini', 'claude-sonnet-4-6', 'llama3'.",
    )
    api_key_encrypted = models.TextField(
        blank=True,
        default="",
        help_text="Stored encrypted. Leave blank for providers that don't need a key (e.g. local Ollama).",
    )
    base_url = models.URLField(
        blank=True,
        default="",
        help_text="For 'custom' (OpenAI-compatible): the base URL, e.g. "
                   "https://api.deepseek.com/v1 — see the provider field's help text for "
                   "presets. For 'generic': the FULL endpoint URL to POST to. "
                   "For self-hosted Ollama: only needed if not on the default local address.",
    )
    is_active = models.BooleanField(
        default=False,
        help_text="Only one provider can be active at a time. Activating this one "
                   "deactivates all others. Leave all inactive to disable AI features "
                   "everywhere (modules fall back to normal non-AI behaviour).",
    )
    request_timeout_seconds = models.PositiveIntegerField(default=60)

    # ── Generic / fully-custom provider template (only used when
    #    provider == "generic") ─────────────────────────────────────────
    # This lets an admin plug in literally ANY HTTP+JSON AI API — including
    # ones that don't exist yet — from the Django admin alone, with zero
    # code changes and zero deployments. Two placeholders are substituted
    # into the templates below: {{prompt}} and {{model}}. {{api_key}} may
    # be used inside header values.
    generic_http_method = models.CharField(
        max_length=10,
        default="POST",
        help_text="HTTP method for the request. Almost always POST.",
    )
    generic_headers_template = models.TextField(
        blank=True,
        default='{\n  "Content-Type": "application/json",\n  "Authorization": "Bearer {{api_key}}"\n}',
        help_text="JSON object of request headers. Use {{api_key}} anywhere the key belongs "
                   "(e.g. in an Authorization or x-api-key header).",
    )
    generic_body_template = models.TextField(
        blank=True,
        default='{\n  "model": "{{model}}",\n  "messages": [{"role": "user", "content": "{{prompt}}"}]\n}',
        help_text="JSON request body template. Use {{model}} and {{prompt}} as placeholders — "
                   "they are safely substituted as JSON strings (no manual escaping needed).",
    )
    generic_response_text_path = models.CharField(
        max_length=200,
        blank=True,
        default="choices.0.message.content",
        help_text="Dotted path to the generated text inside the JSON response, e.g. "
                   "'choices.0.message.content' (OpenAI-style), 'content.0.text' "
                   "(Anthropic-style), or 'candidates.0.content.parts.0.text' (Gemini-style).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "AI Provider Setting"
        verbose_name_plural = "AI Provider Settings"
        ordering = ["-is_active", "name"]

    def __str__(self):
        flag = "🟢 ACTIVE" if self.is_active else "—"
        return f"{self.name} ({self.get_provider_display()}) {flag}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            # Enforce single-active invariant at the DB level after save,
            # so there's never a window where two rows are active.
            AIProviderSettings.objects.exclude(pk=self.pk).update(is_active=False)

    # ── Key handling (delegates to ai_registry's Fernet helpers) ──────────
    def set_api_key(self, raw_key: str):
        """Encrypt and store a plaintext API key. Call .save() after."""
        from core.ai_registry import encrypt_secret
        self.api_key_encrypted = encrypt_secret(raw_key) if raw_key else ""

    def get_api_key(self) -> str:
        """Decrypt and return the plaintext API key (in-memory only)."""
        from core.ai_registry import decrypt_secret
        if not self.api_key_encrypted:
            return ""
        try:
            return decrypt_secret(self.api_key_encrypted)
        except Exception:
            return ""

    @classmethod
    def get_active(cls):
        """Return the active provider row, or None if AI is disabled."""
        return cls.objects.filter(is_active=True).first()


class SchedulerConstraintToggle(models.Model):
    """
    One row per *category* of autoscheduler constraint (not per individual
    rule). This is the registry the pre-run confirmation screen reads from:
    it lets an admin permanently disable a whole category of constraint
    (e.g. "stop enforcing lecturer soft venue preferences") without deleting
    every individual rule, and it's what the "are you sure?" screen shows
    before each run.

    Rows are auto-created on first access via `ensure_constraint_toggles()`
    in core/scheduling_constraints.py — the CONSTRAINT_DEFS list there is the
    single source of truth for which keys exist; this table just stores the
    persisted is_enabled flag per key.

    `applies_to` lets the same registry serve both the regular timetable
    autoscheduler and the exam autoscheduler (or resit, ODEL, etc. later)
    without duplicating rows — a key marked 'both' shows up in either
    confirmation screen.
    """
    APPLIES_TO_CHOICES = [
        ('regular', 'Regular timetable autoscheduler only'),
        ('exam', 'Exam autoscheduler only'),
        ('both', 'Both regular and exam autoschedulers'),
    ]

    key = models.SlugField(
        max_length=60,
        unique=True,
        help_text="Stable identifier used in code, e.g. 'venue_blocks', 'lecturer_blocked_slots'.",
    )
    label = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    applies_to = models.CharField(max_length=10, choices=APPLIES_TO_CHOICES, default="regular")
    is_enabled = models.BooleanField(
        default=True,
        help_text=(
            "Persisted default for this constraint category. Can still be "
            "switched off for a single run from the autoscheduler's pre-run "
            "confirmation screen without changing this saved default."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["applies_to", "key"]
        verbose_name = "Scheduler Constraint Toggle"
        verbose_name_plural = "Scheduler Constraint Toggles"

    def __str__(self):
        state = "ON" if self.is_enabled else "OFF"
        return f"[{state}] {self.label} ({self.key})"


class SyncNode(models.Model):
    """
    Singleton model — only one row should ever exist (enforced the same
    way as SiteSettings, via pk=1).

    Switches this installation between two roles for the host/remote data
    sync feature (see export_import/sync_engine.py):

      * HOST   — this machine is the source of truth. It pushes changed
                 data OUT to the configured `remote_url` whenever a sync
                 is triggered (see the "Sync Now" button on the sudo
                 dashboard, or the `sync_now` management command).
      * REMOTE — this machine only receives pushes from a host. It never
                 initiates outbound sync; export_import.views.receive_sync
                 is the inbound endpoint hosts POST to.

    `shared_token` authenticates the push: the HOST sends it in the
    `X-Sync-Token` header, and the REMOTE compares it to its own stored
    value. Both machines must be configured with the SAME token. It is
    encrypted at rest the same way AIProviderSettings encrypts API keys.
    """
    MODE_CHOICES = [
        ("host", "Host — sends data to a remote"),
        ("remote", "Remote — receives data from a host"),
    ]

    mode = models.CharField(
        max_length=10,
        choices=MODE_CHOICES,
        default="host",
        help_text="Whether this installation is the HOST (sends data out) "
                   "or the REMOTE (receives data from a host).",
    )
    is_enabled = models.BooleanField(
        default=False,
        help_text="Master on/off switch. Leave off until both sides are configured "
                   "with the same shared token.",
    )
    remote_url = models.URLField(
        blank=True,
        default="",
        help_text="HOST ONLY: base URL of the remote installation to sync to, "
                   "e.g. https://backup.example.ac.ke — the /export-import/sync/receive/ "
                   "path is appended automatically.",
    )
    shared_token_encrypted = models.TextField(
        blank=True,
        default="",
        help_text="Shared secret used to authenticate sync requests. Stored encrypted. "
                   "Must match on both the host and the remote.",
    )
    sync_batch_delay_seconds = models.FloatField(
        default=1.5,
        help_text="Pause between sending each model's batch during a sync run. "
                   "Keeps a long sync from hammering the remote server or the network "
                   "all at once, and keeps the background worker from hanging.",
    )
    verify_ssl = models.BooleanField(
        default=True,
        help_text="HOST ONLY: verify the remote's TLS certificate against the system's "
                   "trusted CA list. Leave ON in production. Ignored once a certificate "
                   "fingerprint is pinned below — pinning is the stronger, recommended "
                   "way to trust a self-signed/internal remote.",
    )
    pinned_cert_fingerprint = models.CharField(
        max_length=64, blank=True, default="",
        help_text="HOST ONLY: SHA-256 fingerprint (hex) of the remote's TLS certificate, "
                   "pinned via the 'Certificate Pinning' panel. When set, this is checked "
                   "on every connection instead of the system CA trust store — the modern, "
                   "safer alternative to turning Verify SSL off for a self-signed remote.",
    )
    pinned_cert_saved_at = models.DateTimeField(null=True, blank=True)
    auto_sync_interval_minutes = models.PositiveIntegerField(
        default=0,
        help_text="HOST ONLY: as long as sync is enabled and configured, this installation "
                   "already auto-syncs the moment it detects the remote has come back online "
                   "(checked every couple of minutes in the background) — that part is always "
                   "on. This setting is ADDITIONALLY how often to also resync on a fixed "
                   "schedule even while the connection never drops, in minutes. 0 means no "
                   "extra scheduled resync — only the reconnect-triggered one above, plus "
                   "whatever you trigger manually with 'Sync Now' or the sync_now command.",
    )
    last_sync_started_at = models.DateTimeField(null=True, blank=True)
    last_sync_finished_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=20, blank=True, default="")

    # ── Live connection tracking (separate from "did the last data sync
    # succeed" above) — this is "can the two sides currently reach each
    # other at all", checked independently of whether any data has ever
    # been pushed, and re-checked on demand so a dropped network or a
    # paused sync doesn't leave a stale "connected" state on screen.
    last_connection_check_at = models.DateTimeField(
        null=True, blank=True,
        help_text="HOST ONLY: last time this installation actively tested "
                   "connectivity to the configured remote (a lightweight ping, "
                   "not a data push).",
    )
    last_connection_ok = models.BooleanField(
        null=True, blank=True, default=None,
        help_text="HOST ONLY: result of the last connection check.",
    )
    last_connection_error = models.TextField(
        blank=True, default="",
        help_text="HOST ONLY: error message from the last failed connection check.",
    )
    last_contacted_at = models.DateTimeField(
        null=True, blank=True,
        help_text="REMOTE ONLY: last time a host successfully reached this "
                   "installation with a valid shared token (a ping or an actual "
                   "sync push both count).",
    )

    class Meta:
        verbose_name = "Sync Node Settings"
        verbose_name_plural = "Sync Node Settings"

    def __str__(self):
        state = "ENABLED" if self.is_enabled else "disabled"
        return f"Sync Node — {self.get_mode_display()} ({state})"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def set_token(self, raw_token: str):
        from core.ai_registry import encrypt_secret
        self.shared_token_encrypted = encrypt_secret(raw_token) if raw_token else ""

    def get_token(self) -> str:
        from core.ai_registry import decrypt_secret
        if not self.shared_token_encrypted:
            return ""
        try:
            return decrypt_secret(self.shared_token_encrypted)
        except Exception:
            return ""

    @property
    def is_host(self):
        return self.mode == "host"

    @property
    def is_remote(self):
        return self.mode == "remote"

    # Freshness windows used by connection_summary() below.
    _CONNECTION_CHECK_STALE_SECONDS = 120   # HOST: how old a check can be before we call it stale
    _CONTACT_FRESH_SECONDS = 300            # REMOTE: how recently the host must have pinged/synced to count as "connected"

    def connection_summary(self):
        """
        Returns a small dict describing whether the other side of this
        sync pair is currently reachable, from THIS installation's point
        of view:

          {'state': 'connected'|'disconnected'|'unknown'|'unconfigured'|'disabled',
           'label': short status text,
           'detail': one more sentence of context,
           'stale': bool}  # HOST only — whether the last check is old enough to re-check

        HOST: based on the last active connection check (see
        export_import.sync_engine.test_connection()), since a host can
        actively probe its configured remote.

        REMOTE: a remote never initiates outbound calls, so "connected"
        here means "a host has successfully reached me recently" — based
        on `last_contacted_at`, updated by the /sync/ping/ and
        /sync/receive/ endpoints whenever a valid token comes in.
        """
        from django.utils.timesince import timesince
        now = timezone.now()

        if not self.is_enabled:
            return {"state": "disabled", "label": "Sync disabled",
                    "detail": "Sync is turned off for this installation.", "stale": False}

        if self.is_host:
            if not self.remote_url or not self.shared_token_encrypted:
                return {"state": "unconfigured", "label": "Not configured",
                        "detail": "Set a remote URL and shared token in Sync Settings.", "stale": False}
            if self.last_connection_check_at is None:
                return {"state": "unknown", "label": "Not checked yet",
                        "detail": "Waiting for the first connection check.", "stale": True}
            age = (now - self.last_connection_check_at).total_seconds()
            stale = age > self._CONNECTION_CHECK_STALE_SECONDS
            ago = timesince(self.last_connection_check_at, now)
            if self.last_connection_ok:
                return {"state": "connected", "label": "Connected to remote",
                        "detail": f"Last verified {ago} ago.", "stale": stale}
            return {"state": "disconnected", "label": "Cannot reach remote",
                    "detail": self.last_connection_error or f"Connection check failed {ago} ago.", "stale": stale}

        # REMOTE
        if not self.shared_token_encrypted:
            return {"state": "unconfigured", "label": "Not configured",
                    "detail": "Set the shared token in Sync Settings to accept pushes.", "stale": False}
        if self.last_contacted_at is None:
            return {"state": "unknown", "label": "Awaiting host",
                    "detail": "No host has contacted this installation yet.", "stale": False}
        age = (now - self.last_contacted_at).total_seconds()
        ago = timesince(self.last_contacted_at, now)
        if age <= self._CONTACT_FRESH_SECONDS:
            return {"state": "connected", "label": "Host is connected",
                    "detail": f"Last contact {ago} ago.", "stale": False}
        return {"state": "disconnected", "label": "No recent contact",
                "detail": f"Last contact {ago} ago — host may be offline, paused, or unreachable.", "stale": False}


class SyncRun(models.Model):
    """
    One row per sync attempt (a click of "Sync Now", or a scheduled run).
    `SyncModelLog` rows below record the per-model detail of what was
    sent within this run.
    """
    STATUS_CHOICES = [
        ("running", "Running"),
        ("success", "Completed"),
        ("partial", "Completed with errors"),
        ("failed", "Failed"),
    ]

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="running")
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Superuser who clicked 'Sync Now', or blank for an automated run.",
    )
    remote_url = models.URLField(blank=True, default="")
    peer_run_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="On the HOST: not used. On the REMOTE: the host's SyncRun id for this "
                   "push batch, so incoming model logs from the same sync run can be "
                   "grouped together here even though the two sides have separate "
                   "SyncRun tables.",
    )
    total_models = models.PositiveIntegerField(default=0)
    models_completed = models.PositiveIntegerField(default=0)
    records_sent = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Sync Run"
        verbose_name_plural = "Sync Runs"

    def __str__(self):
        return f"SyncRun #{self.pk} [{self.status}] {self.started_at:%Y-%m-%d %H:%M}"


class SyncModelLog(models.Model):
    """
    Per-model line item within a SyncRun — the log of "which data was
    sent" the admin asked for. Shows the dependency-safe order the model
    was sent in (department -> program -> program_course -> ... etc.),
    how many rows were new/changed vs skipped as unchanged, and whether
    the remote accepted them.
    """
    STATUS_CHOICES = [
        ("sent", "Sent"),
        ("skipped", "No changes — skipped"),
        ("partial", "Partially sent — some rows rejected"),
        ("failed", "Failed"),
    ]

    run = models.ForeignKey(SyncRun, on_delete=models.CASCADE, related_name="model_logs")
    order = models.PositiveIntegerField(help_text="Position in the dependency-safe send order.")
    app_label = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100)
    records_changed = models.PositiveIntegerField(default=0)
    records_total = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="sent")
    error_message = models.TextField(blank=True, default="")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["run", "order"]
        verbose_name = "Sync Model Log"
        verbose_name_plural = "Sync Model Logs"

    @property
    def label(self):
        return f"{self.app_label}.{self.model_name}"

    def __str__(self):
        return f"{self.label} — {self.status} ({self.records_changed} changed)"


class SyncRecordState(models.Model):
    """
    HOST-side bookkeeping: the last content hash of every row that has
    ever been synced, per model. On the next sync run, the host
    recomputes each row's current hash and only sends rows whose hash
    is new or different from what's stored here — that's the "compare
    what's in the host vs what's in the remote, and if it changed,
    update the remote" behaviour. After a row is sent successfully its
    hash here is updated, so unchanged data is never re-sent.
    """
    app_label = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    content_hash = models.CharField(max_length=64)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("app_label", "model_name", "object_id")
        indexes = [models.Index(fields=["app_label", "model_name"])]
        verbose_name = "Sync Record State"
        verbose_name_plural = "Sync Record States"

    def __str__(self):
        return f"{self.app_label}.{self.model_name}#{self.object_id}"


class ActivityLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=10)
    app_label = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    data = models.JSONField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]