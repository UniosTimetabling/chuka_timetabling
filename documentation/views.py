from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import View
from django.db.models import Q, Count, F
from django.core.paginator import Paginator
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from datetime import datetime, timedelta
import json

from .models import (
    DocumentationCategory, 
    DocumentationPage, 
    DocumentationSection,
    CodeExample,
    DocumentationViewLog
)

class DocumentationView(LoginRequiredMixin, View):
    """Unified view for all documentation pages"""
    template_name = 'documentation/single_page.html'
    
    @method_decorator(ratelimit(key='user_or_ip', rate='30/m', method='GET', block=True))
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(request)
        
        # Determine which content to show based on URL
        slug = kwargs.get('slug', None)
        
        if 'page/' in request.path and slug:
            return self.render_page_detail(request, slug, context)
        elif 'category/' in request.path and slug:
            return self.render_category(request, slug, context)
        elif 'search' in request.path:
            return self.render_search(request, context)
        elif 'list' in request.path:
            return self.render_page_list(request, context)
        else:
            return self.render_home(request, context)
    
    def get_context_data(self, request):
        """Get common context for all views"""
        context = {}
        
        # Get accessible categories
        categories = DocumentationCategory.objects.filter(is_active=True).order_by('order', 'name')
        accessible_categories = []
        
        for category in categories:
            if category.can_access(request.user):
                category_data = {
                    'id': category.id,
                    'name': category.name,
                    'slug': category.slug,
                    'icon': category.icon,
                    'description': category.description,
                    'page_count': category.get_published_pages_count(),
                }
                accessible_categories.append(category_data)
        
        context['categories'] = accessible_categories
        
        # Get recent activity
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent_pages = DocumentationPage.objects.filter(
            is_published=True,
            updated_at__gte=thirty_days_ago
        ).order_by('-updated_at')[:10]
        
        context['recent_updates'] = recent_pages
        
        # Get popular pages
        popular_pages = DocumentationPage.objects.filter(
            is_published=True
        ).order_by('-views')[:10]
        
        context['popular_pages'] = popular_pages
        
        # User stats
        if request.user.is_authenticated:
            context['user_view_count'] = DocumentationViewLog.objects.filter(
                user=request.user
            ).count()
            
            # Recently viewed pages - FIXED: removed distinct('page')
            # Get unique pages by using values() and distinct() on page_id
            recently_viewed_page_ids = DocumentationViewLog.objects.filter(
                user=request.user
            ).order_by('-timestamp').values_list('page_id', flat=True).distinct()[:5]
            
            # Get the actual page objects
            recently_viewed_pages = DocumentationPage.objects.filter(
                id__in=recently_viewed_page_ids,
                is_published=True
            )
            
            context['recently_viewed'] = recently_viewed_pages
        
        # System stats
        context['total_pages'] = DocumentationPage.objects.filter(is_published=True).count()
        context['total_categories'] = len(accessible_categories)
        
        return context
    
    def render_home(self, request, context):
        """Render home page"""
        context['view_type'] = 'home'
        
        # Get featured pages
        featured_pages = DocumentationPage.objects.filter(
            is_published=True
        ).order_by('-views', '-updated_at')[:6]
        
        context['featured_pages'] = featured_pages
        
        # Get page counts by type
        page_types = DocumentationPage.PAGE_TYPES
        type_counts = []
        for page_type in page_types:
            count = DocumentationPage.objects.filter(
                is_published=True,
                page_type=page_type[0]
            ).count()
            if count > 0:
                type_counts.append({
                    'type': page_type[0],
                    'name': page_type[1],
                    'count': count,
                })
        
        context['type_counts'] = type_counts
        
        # Quick access links
        quick_links = DocumentationPage.objects.filter(
            is_published=True,
            page_type__in=['guide', 'tutorial', 'faq']
        ).order_by('?')[:8]  # Random selection
        
        context['quick_links'] = quick_links
        
        return render(request, self.template_name, context)
    
    def render_page_detail(self, request, slug, context):
        """Render individual page detail"""
        page = get_object_or_404(DocumentationPage, slug=slug, is_published=True)
        
        # Check access
        if not page.can_access(request.user):
            messages.error(request, "You don't have permission to view this page.")
            return redirect('documentation:home')
        
        # Increment views and log
        page.increment_views()
        DocumentationViewLog.objects.create(
            page=page,
            user=request.user,
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
        
        context.update({
            'view_type': 'page_detail',
            'page': page,
            'sections': page.get_sections(),
            'page_type_display': page.get_page_type_display(),
        })
        
        # Get related pages
        related_pages = page.related_pages.filter(is_published=True)[:6]
        context['related_pages'] = related_pages
        
        # Get prerequisites
        context['prerequisites'] = page.prerequisites.filter(is_published=True)
        
        # Get table of contents
        sections = page.get_sections()
        toc = [{'id': f"section-{section.id}", 'title': section.title} for section in sections]
        context['toc'] = toc
        
        # Get navigation
        all_pages_in_category = list(DocumentationPage.objects.filter(
            category=page.category,
            is_published=True
        ).order_by('order', 'title'))
        
        current_index = next((i for i, p in enumerate(all_pages_in_category) if p.id == page.id), -1)
        
        if current_index > 0:
            context['prev_page'] = all_pages_in_category[current_index - 1]
        
        if current_index < len(all_pages_in_category) - 1:
            context['next_page'] = all_pages_in_category[current_index + 1]
        
        return render(request, self.template_name, context)
    
    def render_category(self, request, slug, context):
        """Render category page"""
        category = get_object_or_404(DocumentationCategory, slug=slug, is_active=True)
        
        # Check access
        if not category.can_access(request.user):
            messages.error(request, "You don't have permission to view this category.")
            return redirect('documentation:home')
        
        # Get pages in this category
        pages = category.get_published_pages()
        
        # Apply filters
        page_type = request.GET.get('type')
        if page_type:
            pages = pages.filter(page_type=page_type)
        
        difficulty = request.GET.get('difficulty')
        if difficulty:
            pages = pages.filter(difficulty=difficulty)
        
        # Search within category
        query = request.GET.get('q', '')
        if query:
            pages = pages.filter(
                Q(title__icontains=query) |
                Q(short_description__icontains=query) |
                Q(content__icontains=query)
            )
        
        # Order
        order_by = request.GET.get('order', 'order')
        if order_by == 'title':
            pages = pages.order_by('title')
        elif order_by == 'views':
            pages = pages.order_by('-views')
        elif order_by == 'recent':
            pages = pages.order_by('-updated_at')
        else:
            pages = pages.order_by('order', 'title')
        
        # Paginate
        paginator = Paginator(pages, 20)
        page_number = request.GET.get('page', 1)
        page_obj = paginator.get_page(page_number)
        
        context.update({
            'view_type': 'category',
            'category': category,
            'pages': page_obj,
            'page_obj': page_obj,
            'paginator': paginator,
            'current_type': page_type,
            'current_difficulty': difficulty,
            'current_query': query,
            'current_order': order_by,
            'page_types': DocumentationPage.PAGE_TYPES,
            'difficulty_levels': DocumentationPage.DIFFICULTY_CHOICES,
        })
        
        return render(request, self.template_name, context)
    
    def render_page_list(self, request, context):
        """Render all pages list"""
        pages = DocumentationPage.objects.filter(is_published=True)
        
        # Apply filters
        category_slug = request.GET.get('category')
        if category_slug:
            pages = pages.filter(category__slug=category_slug)
        
        page_type = request.GET.get('type')
        if page_type:
            pages = pages.filter(page_type=page_type)
        
        difficulty = request.GET.get('difficulty')
        if difficulty:
            pages = pages.filter(difficulty=difficulty)
        
        # Search
        query = request.GET.get('q', '')
        if query:
            pages = pages.filter(
                Q(title__icontains=query) |
                Q(short_description__icontains=query) |
                Q(content__icontains=query)
            )
        
        # Order
        order_by = request.GET.get('order', 'title')
        if order_by == 'title':
            pages = pages.order_by('title')
        elif order_by == 'views':
            pages = pages.order_by('-views')
        elif order_by == 'recent':
            pages = pages.order_by('-updated_at')
        elif order_by == 'category':
            pages = pages.order_by('category__order', 'order', 'title')
        
        # Paginate
        paginator = Paginator(pages, 30)
        page_number = request.GET.get('page', 1)
        page_obj = paginator.get_page(page_number)
        
        context.update({
            'view_type': 'list',
            'pages': page_obj,
            'page_obj': page_obj,
            'paginator': paginator,
            'current_category': category_slug,
            'current_type': page_type,
            'current_difficulty': difficulty,
            'current_query': query,
            'current_order': order_by,
            'total_results': pages.count(),
            'page_types': DocumentationPage.PAGE_TYPES,
            'difficulty_levels': DocumentationPage.DIFFICULTY_CHOICES,
        })
        
        return render(request, self.template_name, context)
    
    def render_search(self, request, context):
        """Render search results"""
        query = request.GET.get('q', '').strip()
        
        if not query:
            return redirect('documentation:list')
        
        # Search in pages
        pages = DocumentationPage.objects.filter(
            is_published=True
        ).filter(
            Q(title__icontains=query) |
            Q(short_description__icontains=query) |
            Q(content__icontains=query) |
            Q(sections__title__icontains=query) |
            Q(sections__content__icontains=query)
        ).distinct()
        
        # Apply additional filters
        category_slug = request.GET.get('category')
        if category_slug:
            pages = pages.filter(category__slug=category_slug)
        
        page_type = request.GET.get('type')
        if page_type:
            pages = pages.filter(page_type=page_type)
        
        # Order results
        order_by = request.GET.get('order', 'relevance')
        if order_by == 'title':
            pages = pages.order_by('title')
        elif order_by == 'views':
            pages = pages.order_by('-views')
        elif order_by == 'recent':
            pages = pages.order_by('-updated_at')
        
        # Paginate
        paginator = Paginator(pages, 20)
        page_number = request.GET.get('page', 1)
        page_obj = paginator.get_page(page_number)
        
        context.update({
            'view_type': 'search',
            'pages': page_obj,
            'page_obj': page_obj,
            'paginator': paginator,
            'query': query,
            'current_category': category_slug,
            'current_type': page_type,
            'current_order': order_by,
            'total_results': pages.count(),
            'page_types': DocumentationPage.PAGE_TYPES,
        })
        
        return render(request, self.template_name, context)

class QuickSearchView(LoginRequiredMixin, View):
    """AJAX endpoint for quick search suggestions"""
    
    @method_decorator(ratelimit(key='user_or_ip', rate='30/m', method='GET', block=True))
    def get(self, request):
        query = request.GET.get('q', '').strip()
        
        if len(query) < 2:
            return JsonResponse({'results': []})
        
        # Search in accessible pages
        pages = DocumentationPage.objects.filter(
            is_published=True,
            title__icontains=query
        )[:10]
        
        results = []
        for page in pages:
            if page.can_access(request.user):
                results.append({
                    'id': page.id,
                    'title': page.title,
                    'url': page.get_absolute_url(),
                    'category': page.category.name,
                    'type': page.get_page_type_display(),
                    'description': page.short_description[:100] if page.short_description else ''
                })
        
        return JsonResponse({'results': results})

class PageStatsView(LoginRequiredMixin, View):
    """Get page statistics (AJAX)"""
    
    def get(self, request, slug):
        page = get_object_or_404(DocumentationPage, slug=slug)
        
        # Check access
        if not page.can_access(request.user):
            return JsonResponse({'error': 'Access denied'}, status=403)
        
        # Get view stats
        today = timezone.now().date()
        week_ago = today - timedelta(days=7)
        
        daily_views = DocumentationViewLog.objects.filter(
            page=page,
            timestamp__date=today
        ).count()
        
        weekly_views = DocumentationViewLog.objects.filter(
            page=page,
            timestamp__date__gte=week_ago
        ).count()
        
        total_views = page.views
        
        return JsonResponse({
            'daily_views': daily_views,
            'weekly_views': weekly_views,
            'total_views': total_views,
            'read_time': page.estimated_read_time,
            'version': page.version,
            'last_updated': page.updated_at.strftime('%Y-%m-%d %H:%M') if page.updated_at else ''
        })