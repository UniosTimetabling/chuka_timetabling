"""
Audit Context Middleware - Captures the current user, IP, and request ID
so signal handlers (which don't have access to `request`) can attach
proper attribution to every audit log entry.
"""
import uuid
from .signals import set_audit_context, clear_audit_context


class AuditContextMiddleware:
    """
    Add to MIDDLEWARE in settings.py, AFTER AuthenticationMiddleware:

        MIDDLEWARE = [
            ...
            'django.contrib.auth.middleware.AuthenticationMiddleware',
            'backup_system.middleware.AuditContextMiddleware',
            ...
        ]
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and not user.is_authenticated:
            user = None

        ip_address = self._get_client_ip(request)
        request_id = str(uuid.uuid4())
        session_id = request.session.session_key or ''
        user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]

        set_audit_context(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            session_id=session_id,
        )

        try:
            response = self.get_response(request)
        finally:
            clear_audit_context()

        return response

    @staticmethod
    def _get_client_ip(request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')
