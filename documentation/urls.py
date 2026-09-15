from django.urls import path
from . import views

app_name = 'documentation'

urlpatterns = [
    # Main unified view - handles all documentation pages
    path('home/', views.DocumentationView.as_view(), name='home'),  
    
    path('list/', views.DocumentationView.as_view(), name='list'),
    path('search/', views.DocumentationView.as_view(), name='search'),
    path('category/<slug:slug>/', views.DocumentationView.as_view(), name='category'),
    path('page/<slug:slug>/', views.DocumentationView.as_view(), name='page'),
    
    # API endpoints
    path('api/quick-search/', views.QuickSearchView.as_view(), name='quick_search'),
    path('api/stats/<slug:slug>/', views.PageStatsView.as_view(), name='page_stats'),
]