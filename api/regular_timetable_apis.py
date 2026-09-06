from timetable.models import SchedulerConfig, TempTimetable, AutoMergedExamGroup
from room_management.models import Venue
from django.http import JsonResponse
from django.core.exceptions import ObjectDoesNotExist
import json
import traceback

# ======== API: FETCH VENUES ========
def api_get_venues(request):
    """Return venues in JSON for fast frontend rendering"""
    try:
        venues = list(Venue.objects.values("id", "code", "capacity").order_by("code"))
        return JsonResponse({"venues": venues})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ======== API: FETCH TEMP TIMETABLE ========
def api_get_timetable(request):
    """Return timetable data in JSON with proper error handling"""
    try:
        temp_data = TempTimetable.objects.select_related(
            "course_allocation", 
            "venue",
            "course_allocation__lecturer"  # Add this to prefetch lecturer
        ).all()  # Use .all() instead of .only() to avoid missing field errors

        data = []
        for t in temp_data:
            try:
                # Safely get lecturer name with null check
                lecturer_name = None
                if t.course_allocation and t.course_allocation.lecturer:
                    lecturer_name = t.course_allocation.lecturer.name
                
                # Safely get venue code
                venue_code = None
                if t.venue:
                    venue_code = t.venue.code
                
                # Safely get course allocation fields
                course_code = t.course_allocation.course_code if t.course_allocation else "Unknown"
                course_name = t.course_allocation.course_name if t.course_allocation else "Unknown"
                students = t.course_allocation.number_of_students if t.course_allocation else 0
                
                data.append({
                    "id": t.id,  # Add ID for reference
                    "day": t.day,
                    "start_time": t.start_time.strftime("%H:%M") if t.start_time else "",
                    "end_time": t.end_time.strftime("%H:%M") if t.end_time else "",
                    "venue": venue_code,
                    "venue_id": t.venue.id if t.venue else None,
                    "course_code": course_code,
                    "course_name": course_name,
                    "students": students,
                    "lecturer": lecturer_name,
                    "course_allocation_id": t.course_allocation.id if t.course_allocation else None,
                })
            except ObjectDoesNotExist as e:
                print(f"Error processing timetable entry {t.id}: {e}")
                # Add a placeholder for problematic entries
                data.append({
                    "id": t.id,
                    "day": t.day,
                    "start_time": t.start_time.strftime("%H:%M") if t.start_time else "",
                    "end_time": t.end_time.strftime("%H:%M") if t.end_time else "",
                    "venue": t.venue.code if t.venue else None,
                    "course_code": "Error loading",
                    "course_name": f"Missing relation: {str(e)}",
                    "students": 0,
                    "lecturer": None,
                    "error": str(e)
                })
            except Exception as e:
                print(f"Unexpected error processing timetable entry {t.id}: {e}")
                continue

        return JsonResponse({
            "timetable": data,
            "count": len(data)
        })
        
    except Exception as e:
        # Log the full error for debugging
        error_trace = traceback.format_exc()
        print(f"Error in api_get_timetable: {error_trace}")
        
        return JsonResponse({
            "error": str(e),
            "detail": "An error occurred while fetching timetable data"
        }, status=500)


# ======== API: FETCH CONFIG ========
def api_get_config(request):
    """Return scheduler configuration"""
    try:
        config = SchedulerConfig.objects.first()
        if config:
            data = {
                "start_time": config.start_time.strftime("%H:%M") if config.start_time else "08:00",
                "end_time": config.end_time.strftime("%H:%M") if config.end_time else "17:00",
                "slot_size": config.slot_size,
            }
        else:
            data = {
                "start_time": "08:00",
                "end_time": "17:00",
                "slot_size": 2,
            }
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def merged_api(request):
    """Handle AJAX CRUD for AutoMergedExamGroup"""
    try:
        if request.method == "GET":
            data = []
            groups = AutoMergedExamGroup.objects.select_related("venue").prefetch_related("merged_courses").all()
            
            for g in groups:
                try:
                    courses = []
                    if g.merged_courses.exists():
                        courses = [c.course_code for c in g.merged_courses.all() if c]
                    
                    data.append({
                        "id": g.id,
                        "merged_code": g.merged_code,
                        "total_students": g.total_students,
                        "venue": g.venue.code if g.venue else None,
                        "venue_id": g.venue.id if g.venue else None,
                        "start_time": g.start_time.strftime("%H:%M") if g.start_time else "",
                        "end_time": g.end_time.strftime("%H:%M") if g.end_time else "",
                        "date": g.date,
                        "courses": courses,
                    })
                except Exception as e:
                    print(f"Error processing merged group {g.id}: {e}")
                    data.append({
                        "id": g.id,
                        "merged_code": g.merged_code,
                        "total_students": g.total_students,
                        "error": str(e)
                    })
            
            return JsonResponse({"data": data})

        elif request.method == "POST":
            try:
                if request.content_type == 'application/json':
                    data = json.loads(request.body)
                else:
                    data = request.POST.dict()
                
                merged_code = data.get("merged_code", "")
                total_students = int(data.get("total_students", 0))
                
                group = AutoMergedExamGroup.objects.create(
                    merged_code=merged_code, 
                    total_students=total_students
                )
                return JsonResponse({
                    "id": group.id, 
                    "status": "created",
                    "message": "Group created successfully"
                })
            except Exception as e:
                return JsonResponse({
                    "error": str(e),
                    "status": "error"
                }, status=400)

        elif request.method == "PUT":
            try:
                body = json.loads(request.body)
                group = AutoMergedExamGroup.objects.get(id=body["id"])
                
                if "merged_code" in body:
                    group.merged_code = body["merged_code"]
                if "total_students" in body:
                    group.total_students = body["total_students"]
                if "date" in body:
                    group.date = body["date"]
                if "start_time" in body and body["start_time"]:
                    from datetime import datetime
                    group.start_time = datetime.strptime(body["start_time"], "%H:%M").time()
                if "end_time" in body and body["end_time"]:
                    from datetime import datetime
                    group.end_time = datetime.strptime(body["end_time"], "%H:%M").time()
                if "venue_id" in body and body["venue_id"]:
                    group.venue_id = body["venue_id"]
                
                group.save()
                return JsonResponse({
                    "status": "updated",
                    "message": "Group updated successfully"
                })
            except AutoMergedExamGroup.DoesNotExist:
                return JsonResponse({"error": "Group not found"}, status=404)
            except Exception as e:
                return JsonResponse({"error": str(e)}, status=400)

        elif request.method == "DELETE":
            try:
                body = json.loads(request.body)
                deleted, _ = AutoMergedExamGroup.objects.filter(id=body["id"]).delete()
                if deleted:
                    return JsonResponse({
                        "status": "deleted",
                        "message": "Group deleted successfully"
                    })
                else:
                    return JsonResponse({"error": "Group not found"}, status=404)
            except Exception as e:
                return JsonResponse({"error": str(e)}, status=400)

        return JsonResponse({"error": "Method not allowed"}, status=405)
        
    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Error in merged_api: {error_trace}")
        return JsonResponse({"error": str(e)}, status=500)


