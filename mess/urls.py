from django.urls import path
from . import views as mess
urlpatterns=[
    #====================mess operations=======================================#
    path("mess/", mess.food_list_user, name="food_list_user"),
    path("api/add-food/", mess.add_food, name="add_food"),
    path("api/delete-food/<int:pk>/", mess.delete_food, name="delete_food"),
    path("api/offers/", mess.offers_api, name="offers_api"),
    path("mess-admin/", mess.food_admin, name="food_admin"),
    path('admin/offers/', mess.food_admin, name='offers_admin'),
    path('api/add-offer/', mess.add_offer, name='add_offer'),
    path('api/delete-offer/<int:offer_id>/', mess.delete_offer, name='delete_offer'),
    path('api/edit-offer/<int:offer_id>/', mess.edit_offer, name='edit_offer'),
]