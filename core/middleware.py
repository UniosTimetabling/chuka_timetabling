from django.utils.deprecation import MiddlewareMixin
import logging

# Import from your signals
from core.signals import set_current_user, get_current_user

request_logger = logging.getLogger("django.request")
app_logger = logging.getLogger("app")

class CurrentUserMiddleware(MiddlewareMixin):
    """Attach current user to threadlocal for signal use"""
    
    def process_request(self, request):
        """Set current user at the beginning of request"""
        if hasattr(request, 'user') and request.user.is_authenticated:
            set_current_user(request.user)
        else:
            set_current_user(None)

    def process_response(self, request, response):
        """Clear current user at the end of request"""
        set_current_user(None)
        return response

    def process_exception(self, request, exception):
        """Clear current user if there's an exception"""
        set_current_user(None)


class RequestLoggingMiddleware(MiddlewareMixin):
    """
    Centralized request/error logging.

    - Logs every unhandled exception that reaches Django's error handling
      (i.e. every 500) to the 'django.request' logger -> logs/errors.log,
      with the request method, path, user, and IP attached so failures
      can be traced back to who hit what.
    - This runs alongside (does not replace) Django's default 500 handling
      and the project's custom `handler500 = views.universal_error`.
    """

    def process_exception(self, request, exception):
        user = getattr(request, "user", None)
        username = user.username if user and user.is_authenticated else "anonymous"
        request_logger.error(
            "Unhandled exception on %s %s | user=%s | ip=%s | %s: %s",
            request.method,
            request.get_full_path(),
            username,
            self._client_ip(request),
            exception.__class__.__name__,
            exception,
            exc_info=True,
        )
        # Returning None lets Django continue its normal exception handling
        # (i.e. still renders handler500 / propagates as usual).
        return None

    @staticmethod
    def _client_ip(request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")


class SecurityHeadersMiddleware(MiddlewareMixin):
    """Add security headers to responses"""
    
    def process_response(self, request, response):
        """Add security headers to every response"""
        # Security Headers
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['X-XSS-Protection'] = '1; mode=block'
        response['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        response['Referrer-Policy'] = 'same-origin'
        
        # Remove server header for security
        if 'Server' in response:
            del response['Server']
            
        return response

# from .models import VisitorLog
# from django.utils.deprecation import MiddlewareMixin

# class IPVisitorMiddleware(MiddlewareMixin):
#     def process_request(self, request):
#         ip = self.get_client_ip(request)
#         user_agent = request.META.get('HTTP_USER_AGENT', '')

#         # Use session to avoid duplicate logs for the same user in one session
#         if not request.session.get("visitor_logged", False):
#             if ip:
#                 VisitorLog.objects.create(ip_address=ip, user_agent=user_agent)
#                 request.session["visitor_logged"] = True

#     def get_client_ip(self, request):
#         """Get client IP even if behind proxy or load balancer"""
#         x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
#         if x_forwarded_for:
#             ip = x_forwarded_for.split(",")[0].strip()
#         else:
#             ip = request.META.get("REMOTE_ADDR", "")
#         return ip
