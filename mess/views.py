from django.contrib import messages
from .models import Food, FoodImage, FoodOption
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from .models import Food, FoodImage, FoodOption,Offer
from django.contrib.auth.decorators import login_required
@login_required
def food_admin(request):
    foods = Food.objects.all().prefetch_related("images", "options")
    offers = Offer.objects.all().order_by('-start_date')
    return render(request, "mess/admin.html", {"foods": foods, "offers": offers})

@csrf_exempt
def add_food(request):
    if request.method == "POST":
        name = request.POST.get("name")
        description = request.POST.get("description", "")
        price = request.POST.get("price", 0)
        food = Food.objects.create(name=name, description=description, price=price)

        # Save images
        for f in request.FILES.getlist("images"):
            FoodImage.objects.create(food=food, image=f)

        # Save options
        for opt in request.POST.getlist("options"):
            if opt.strip():
                FoodOption.objects.create(food=food, option=opt)

        return JsonResponse({"success": True, "id": food.id, "name": food.name, "price": str(food.price)})

    return JsonResponse({"success": False}, status=400)

@csrf_exempt
def delete_food(request, pk):
    if request.method == "POST":
        food = get_object_or_404(Food, id=pk)
        food.delete()
        return JsonResponse({"success": True})
    return JsonResponse({"success": False}, status=400)

from django.http import JsonResponse
from .models import Offer

def offers_api(request):
    offers = Offer.objects.all().order_by("-created_at")
    data = [
        {
            "title": offer.title,
            "description": offer.description,
            "discount": offer.discount,
            "end_date": offer.end_date.strftime("%Y-%m-%d") if offer.end_date else None,
            "image": offer.image.url if offer.image else None,
        }
        for offer in offers
    ]
    return JsonResponse({"offers": data})



def food_list_user(request):
    foods = Food.objects.prefetch_related("images", "options").all()
    return render(request, "mess/user_list.html", {"foods": foods})


from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from .models import Offer
from django.views.decorators.csrf import csrf_exempt
import datetime


# Add Offer (AJAX)
@csrf_exempt
def add_offer(request):
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description', '')
        discount = request.POST.get('discount') or None
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        image = request.FILES.get('image')

        offer = Offer.objects.create(
            title=title,
            description=description,
            discount=discount,
            start_date=start_date,
            end_date=end_date,
            image=image
        )
        return JsonResponse({
            'success': True,
            'id': offer.id,
            'title': offer.title,
            'description': offer.description,
            'discount': offer.discount,
            'start_date': offer.start_date,
            'end_date': offer.end_date
        })
    return JsonResponse({'success': False})


# Delete Offer (AJAX)
@csrf_exempt
def delete_offer(request, offer_id):
    if request.method == 'POST':
        try:
            offer = Offer.objects.get(pk=offer_id)
            offer.delete()
            return JsonResponse({'success': True})
        except Offer.DoesNotExist:
            return JsonResponse({'success': False})
    return JsonResponse({'success': False})


# Edit / Update Offer (AJAX)
@csrf_exempt
def edit_offer(request, offer_id):
    offer = get_object_or_404(Offer, pk=offer_id)
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description', '')
        discount = request.POST.get('discount') or None
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        image = request.FILES.get('image')

        offer.title = title
        offer.description = description
        offer.discount = discount
        offer.start_date = start_date
        offer.end_date = end_date
        if image:
            offer.image = image
        offer.save()

        return JsonResponse({
            'success': True,
            'id': offer.id,
            'title': offer.title,
            'description': offer.description,
            'discount': offer.discount,
            'start_date': offer.start_date,
            'end_date': offer.end_date
        })
    return JsonResponse({'success': False})
