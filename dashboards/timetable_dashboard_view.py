from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib import messages

@login_required
def timetable_dashboard_view(request):
    """
    Main timetable dashboard view with Django messages support
    """
   
    return render(request, "dashboard/timetabling_dashboard.html")