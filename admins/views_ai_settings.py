"""
admins/views_ai_settings.py
============================
Sudo-dashboard UI for managing the central AI provider registry
(core.models.AIProviderSettings). This is a friendlier, dashboard-themed
alternative to using raw Django admin for the same table — any module
in the project (Smart Importer, future AI features) reads whichever
provider is marked active here via core.ai_registry.get_ai_client().

SECURITY:
- Sudo-only (superuser + authenticated), same guard as the rest of
  admins/*.
- The raw API key is accepted only via POST (never GET/query string),
  is immediately encrypted through AIProviderSettings.set_api_key()
  before being saved, and is never echoed back in the response or
  rendered into the page — only a masked preview via mask_key().
- Activating a provider deactivates all others (handled in the model).
"""

from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.ai_registry import mask_key
from core.models import AIProviderSettings

logger = logging.getLogger(__name__)


def _sudo(view_fn):
    return login_required(user_passes_test(lambda u: u.is_superuser)(view_fn))


@_sudo
def ai_settings_view(request):
    """List all configured providers + a form to add/edit one."""
    providers = AIProviderSettings.objects.all()
    rows = []
    for p in providers:
        rows.append({
            "obj": p,
            "masked_key": mask_key(p.get_api_key()) if p.api_key_encrypted else "",
        })
    return render(request, "admins/ai_settings.html", {
        "providers": rows,
        "provider_choices": AIProviderSettings.PROVIDER_CHOICES,
        "active": AIProviderSettings.get_active(),
        "openai_compatible_presets": AIProviderSettings.OPENAI_COMPATIBLE_PRESETS,
        "generic_defaults": {
            "http_method": "POST",
            "headers_template": AIProviderSettings._meta.get_field("generic_headers_template").default,
            "body_template": AIProviderSettings._meta.get_field("generic_body_template").default,
            "response_text_path": AIProviderSettings._meta.get_field("generic_response_text_path").default,
        },
    })


@_sudo
@require_POST
def ai_settings_save(request):
    """Create or update a provider row. API key is encrypted server-side."""
    pk = request.POST.get("id", "").strip()
    name = request.POST.get("name", "").strip()
    provider = request.POST.get("provider", "").strip()
    model_name = request.POST.get("model_name", "").strip()
    base_url = request.POST.get("base_url", "").strip()
    api_key = request.POST.get("api_key", "")  # plaintext, in-memory only, encrypted below
    make_active = request.POST.get("is_active") == "on"
    timeout = request.POST.get("request_timeout_seconds", "60").strip()

    # Only meaningful when provider == "generic", but harmless to read
    # regardless — unused fields are simply ignored by ai_registry for
    # every other provider type.
    generic_http_method = request.POST.get("generic_http_method", "POST").strip() or "POST"
    generic_headers_template = request.POST.get("generic_headers_template", "")
    generic_body_template = request.POST.get("generic_body_template", "")
    generic_response_text_path = request.POST.get("generic_response_text_path", "").strip()

    if not name or not provider or not model_name:
        messages.error(request, "Name, provider, and model name are required.")
        return redirect("sudo_ai_settings")

    try:
        timeout_val = max(5, min(int(timeout or 60), 300))
    except ValueError:
        timeout_val = 60

    if provider == "generic":
        for label, value in (
            ("request body template", generic_body_template),
            ("headers template", generic_headers_template),
        ):
            try:
                json.loads(value or "{}")
            except json.JSONDecodeError:
                messages.error(request, f"Generic provider: the {label} is not valid JSON.")
                return redirect("sudo_ai_settings")
        if not base_url:
            messages.error(request, "Generic provider: the full endpoint URL (Base URL field) is required.")
            return redirect("sudo_ai_settings")

    if pk:
        obj = AIProviderSettings.objects.filter(pk=pk).first()
        if not obj:
            messages.error(request, "Provider not found.")
            return redirect("sudo_ai_settings")
    else:
        obj = AIProviderSettings()

    obj.name = name
    obj.provider = provider
    obj.model_name = model_name
    obj.base_url = base_url
    obj.request_timeout_seconds = timeout_val
    obj.is_active = make_active
    obj.generic_http_method = generic_http_method
    obj.generic_headers_template = generic_headers_template
    obj.generic_body_template = generic_body_template
    obj.generic_response_text_path = generic_response_text_path

    if api_key:
        obj.set_api_key(api_key)  # encrypts before storing; never persisted as plaintext

    obj.save()
    messages.success(request, f"Saved AI provider '{obj.name}'.")
    return redirect("sudo_ai_settings")


@_sudo
@require_POST
def ai_settings_activate(request, pk):
    """Make this the single active provider (deactivates the rest)."""
    obj = AIProviderSettings.objects.filter(pk=pk).first()
    if not obj:
        return JsonResponse({"error": "Not found."}, status=404)
    obj.is_active = True
    obj.save()
    messages.success(request, f"'{obj.name}' is now the active AI provider.")
    return redirect("sudo_ai_settings")


@_sudo
@require_POST
def ai_settings_deactivate_all(request):
    """Turn AI off everywhere. All AI-dependent modules fall back to normal behaviour."""
    AIProviderSettings.objects.update(is_active=False)
    messages.success(request, "AI disabled. Smart Importer and other AI features will use their normal fallback behaviour.")
    return redirect("sudo_ai_settings")


@_sudo
@require_POST
def ai_settings_delete(request, pk):
    obj = AIProviderSettings.objects.filter(pk=pk).first()
    if obj:
        name = obj.name
        obj.delete()
        messages.success(request, f"Deleted provider '{name}'.")
    return redirect("sudo_ai_settings")


@_sudo
@require_POST
def ai_settings_test(request, pk):
    """Send a trivial prompt to verify the provider/key actually works."""
    obj = AIProviderSettings.objects.filter(pk=pk).first()
    if not obj:
        return JsonResponse({"error": "Not found."}, status=404)

    from core.ai_registry import AIClient, AIError
    client = AIClient(obj)
    try:
        reply = client.call("Reply with exactly the word: OK")
        return JsonResponse({"ok": True, "reply": reply[:200]})
    except AIError as e:
        return JsonResponse({"ok": False, "error": str(e)})
    except Exception:
        logger.exception("AI provider test failed for %s", obj.name)
        return JsonResponse({"ok": False, "error": "Unexpected error testing this provider."})