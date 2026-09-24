from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import Feedback
from django.contrib.auth.decorators import login_required
from django_ratelimit.decorators import ratelimit

import json

def feedback_page(request):
    """Render the feedback form page."""
    return render(request, "feedback/feedback.html")

@csrf_exempt
@require_POST
@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def submit_feedback(request):
    """Handle feedback form submissions via POST request."""
    try:
        data = json.loads(request.body)
        full_name = data.get("full_name", "").strip()
        email = data.get("email", "").strip()
        admission_number = data.get("admission_number", "").strip()
        message = data.get("message", "").strip()

        # Validate fields
        if not full_name or not email or not message:
            return JsonResponse({"ok": False, "error": "Please fill in all required fields."}, status=400)

        if len(message) < 10:
            return JsonResponse({"ok": False, "error": "Message is too short. Please provide more details."}, status=400)

        Feedback.objects.create(
            full_name=full_name,
            email=email,
            admission_number=admission_number or None,
            message=message
        )

        return JsonResponse({"ok": True, "message": "Thank you! Your feedback has been submitted successfully."})

    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid data format."}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"An error occurred: {e}"}, status=500)


@login_required
def feedback_panel(request):
    """Display all submitted feedback for timetable staff."""
    query = request.GET.get("q", "").strip()
    feedbacks = Feedback.objects.all()

    if query:
        feedbacks = feedbacks.filter(
            full_name__icontains=query
        ) | feedbacks.filter(
            email__icontains=query
        ) | feedbacks.filter(
            message__icontains=query
        )

    feedbacks = feedbacks.order_by("-created_at")

    return render(request, "feedback/feedback_panel.html", {"feedbacks": feedbacks, "query": query})
