from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.db.models import Q
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.http import require_POST
from datetime import datetime, timedelta
import json
import io
import logging

from .models import Feedback
from .feedback_logger import (
    log_export_feedbacks,
    log_update_status,
    log_export_pdf,
)
from export_import.models import TimetablePdfTemplate
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

logger = logging.getLogger(__name__)
from reportlab.lib import colors
from reportlab.lib.units import inch
import os

def export_feedbacks(request):
    """
    View to select feedbacks for export with filters
    """
    # Get all available years and months from feedback data
    feedbacks = Feedback.objects.all().order_by('-created_at')
    
    # Extract unique years and months for filter dropdowns
    year_month_set = set()
    for feedback in feedbacks:
        year_month_set.add((feedback.created_at.year, feedback.created_at.month))
    
    # Convert to list of dicts for template
    available_periods = []
    for year, month in sorted(year_month_set, reverse=True):
        available_periods.append({
            'year': year,
            'month': month,
            'display': f"{timezone.datetime(year, month, 1).strftime('%B %Y')}"
        })
    
    # Get selected filters from request
    selected_years = request.GET.getlist('years[]', [])
    selected_months = request.GET.getlist('months[]', [])
    search_query = request.GET.get('search', '')
    date_range = request.GET.get('date_range', '')
    selected_ids = request.GET.getlist('selected_ids[]', [])
    
    # Apply filters
    filtered_feedbacks = feedbacks
    
    if search_query:
        filtered_feedbacks = filtered_feedbacks.filter(
            Q(full_name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(admission_number__icontains=search_query) |
            Q(message__icontains=search_query)
        )
    
    if selected_years:
        filtered_feedbacks = filtered_feedbacks.filter(
            created_at__year__in=selected_years
        )
    
    if selected_months:
        # Convert month numbers to integers and filter
        month_ints = [int(m) for m in selected_months]
        filtered_feedbacks = filtered_feedbacks.filter(
            created_at__month__in=month_ints
        )
    
    if date_range:
        if date_range == 'today':
            today = timezone.now().date()
            filtered_feedbacks = filtered_feedbacks.filter(created_at__date=today)
        elif date_range == 'yesterday':
            yesterday = timezone.now().date() - timedelta(days=1)
            filtered_feedbacks = filtered_feedbacks.filter(created_at__date=yesterday)
        elif date_range == 'last_7_days':
            last_week = timezone.now() - timedelta(days=7)
            filtered_feedbacks = filtered_feedbacks.filter(created_at__gte=last_week)
        elif date_range == 'last_30_days':
            last_month = timezone.now() - timedelta(days=30)
            filtered_feedbacks = filtered_feedbacks.filter(created_at__gte=last_month)
    
    # Get individually selected feedbacks
    if selected_ids:
        selected_feedbacks = Feedback.objects.filter(id__in=selected_ids)
        # Combine with filtered results
        filtered_feedbacks = filtered_feedbacks | selected_feedbacks
        filtered_feedbacks = filtered_feedbacks.distinct()
    
    # Paginate results
    paginator = Paginator(filtered_feedbacks, 50)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    context = {
        'feedbacks': page_obj,
        'available_periods': available_periods,
        'selected_years': selected_years,
        'selected_months': selected_months,
        'search_query': search_query,
        'date_range': date_range,
        'selected_ids': selected_ids,
        'total_count': filtered_feedbacks.count(),
    }
    
    # ── Log this action ───────────────────────────────────────────────────────
    log_export_feedbacks(
        filters={
            "search_query": search_query,
            "selected_years": selected_years,
            "selected_months": selected_months,
            "date_range": date_range,
            "selected_ids": selected_ids,
            "page": request.GET.get('page', 1),
        },
        total_count=filtered_feedbacks.count(),
    )

    return render(request, 'feedback/export_feedback.html', context)

@require_POST
def update_feedback_status(request):
    """
    Update feedback status via AJAX
    """
    try:
        data = json.loads(request.body)
        feedback_id = data.get('feedback_id')
        status = data.get('status')
        action = data.get('action')  # 'individual' or 'bulk'
        
        if action == 'individual':
            # Update single feedback
            feedback = Feedback.objects.get(id=feedback_id)
            
            # Map status to database values
            status_map = {
                'seen': 'seen',
                'attended': 'attended', 
                'solved': 'solved'
            }
            
            if status in status_map:
                feedback.status = status_map[status]
                feedback.seen = True
                feedback.save()

                result = {
                    'success': True,
                    'message': f'Feedback marked as {status}',
                    'new_status': feedback.status,
                    'new_status_display': feedback.get_status_display()
                }
                log_update_status(data, action, result)
                return JsonResponse(result)
            else:
                result = {'success': False, 'error': 'Invalid status'}
                log_update_status(data, action, result)
                return JsonResponse(result)
                
        elif action == 'bulk':
            # Update multiple feedbacks
            feedback_ids = data.get('feedback_ids', [])
            status = data.get('status')
            
            if not feedback_ids:
                result = {'success': False, 'error': 'No feedbacks selected'}
                log_update_status(data, action, result)
                return JsonResponse(result)
            
            # Map status to database values
            status_map = {
                'seen': 'seen',
                'attended': 'attended',
                'solved': 'solved'
            }
            
            if status not in status_map:
                result = {'success': False, 'error': 'Invalid status'}
                log_update_status(data, action, result)
                return JsonResponse(result)

            feedbacks = Feedback.objects.filter(id__in=feedback_ids)
            count = feedbacks.count()

            # Update all selected feedbacks
            feedbacks.update(
                status=status_map[status],
                seen=True
            )

            result = {
                'success': True,
                'message': f'Updated {count} feedback(s) to {status}',
                'count': count
            }
            log_update_status(data, action, result)
            return JsonResponse(result)
            
        elif action == 'mark_all_seen':
            # Get filter parameters
            selected_years = data.get('selected_years', [])
            selected_months = data.get('selected_months', [])
            search_query = data.get('search_query', '')
            date_range = data.get('date_range', '')
            
            # Start with all feedbacks
            feedbacks = Feedback.objects.all()
            
            # Apply filters
            if selected_years:
                feedbacks = feedbacks.filter(created_at__year__in=selected_years)
            
            if selected_months:
                month_ints = [int(m) for m in selected_months]
                feedbacks = feedbacks.filter(created_at__month__in=month_ints)
            
            if search_query:
                feedbacks = feedbacks.filter(
                    Q(full_name__icontains=search_query) |
                    Q(email__icontains=search_query) |
                    Q(admission_number__icontains=search_query) |
                    Q(message__icontains=search_query)
                )
            
            if date_range:
                if date_range == 'today':
                    today = timezone.now().date()
                    feedbacks = feedbacks.filter(created_at__date=today)
                elif date_range == 'yesterday':
                    yesterday = timezone.now().date() - timedelta(days=1)
                    feedbacks = feedbacks.filter(created_at__date=yesterday)
                elif date_range == 'last_7_days':
                    last_week = timezone.now() - timedelta(days=7)
                    feedbacks = feedbacks.filter(created_at__gte=last_week)
                elif date_range == 'last_30_days':
                    last_month = timezone.now() - timedelta(days=30)
                    feedbacks = feedbacks.filter(created_at__gte=last_month)
            
            # Update all filtered feedbacks
            count = feedbacks.count()
            feedbacks.update(status='seen', seen=True)

            result = {
                'success': True,
                'message': f'Marked {count} feedback(s) as seen',
                'count': count
            }
            log_update_status(data, action, result)
            return JsonResponse(result)
            
    except Feedback.DoesNotExist:
        result = {'success': False, 'error': 'Feedback not found'}
        log_update_status({}, 'unknown', result)
        return JsonResponse(result)
    except Exception as e:
        result = {'success': False, 'error': str(e)}
        log_update_status({}, 'unknown', result)
        return JsonResponse(result)

def export_feedbacks_pdf(request):
    """
    Export selected feedbacks as PDF with official university template
    """
    try:
        # Get template configuration
        template = TimetablePdfTemplate.get_template()
        
        # Get filter parameters
        selected_years = request.GET.getlist('years[]', [])
        selected_months = request.GET.getlist('months[]', [])
        search_query = request.GET.get('search', '')
        date_range = request.GET.get('date_range', '')
        selected_ids = request.GET.getlist('selected_ids[]', [])
        
        # Start with all feedbacks
        feedbacks = Feedback.objects.all().order_by('-created_at')
        
        # Apply filters
        if selected_years:
            feedbacks = feedbacks.filter(created_at__year__in=selected_years)
        
        if selected_months:
            month_ints = [int(m) for m in selected_months]
            feedbacks = feedbacks.filter(created_at__month__in=month_ints)
        
        if search_query:
            feedbacks = feedbacks.filter(
                Q(full_name__icontains=search_query) |
                Q(email__icontains=search_query) |
                Q(admission_number__icontains=search_query) |
                Q(message__icontains=search_query)
            )
        
        if date_range:
            if date_range == 'today':
                today = timezone.now().date()
                feedbacks = feedbacks.filter(created_at__date=today)
            elif date_range == 'yesterday':
                yesterday = timezone.now().date() - timedelta(days=1)
                feedbacks = feedbacks.filter(created_at__date=yesterday)
            elif date_range == 'last_7_days':
                last_week = timezone.now() - timedelta(days=7)
                feedbacks = feedbacks.filter(created_at__gte=last_week)
            elif date_range == 'last_30_days':
                last_month = timezone.now() - timedelta(days=30)
                feedbacks = feedbacks.filter(created_at__gte=last_month)
        
        # Get individually selected feedbacks
        if selected_ids:
            selected_feedbacks = Feedback.objects.filter(id__in=selected_ids)
            feedbacks = feedbacks | selected_feedbacks
            feedbacks = feedbacks.distinct().order_by('-created_at')
        
        # Create PDF buffer with optimized settings
        buffer = io.BytesIO()
        
        # Use A4 page size with appropriate margins
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=36,  # Smaller margins for more space
            leftMargin=36,
            topMargin=72,
            bottomMargin=72
        )
        
        elements = []
        styles = getSampleStyleSheet()
        
        # Create custom styles with proper text wrapping and larger fonts
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=20,  # Increased from 16
            spaceAfter=20,
            alignment=1,
            fontName='Helvetica-Bold'
        )
        
        subtitle_style = ParagraphStyle(
            'SubtitleStyle',
            parent=styles['Heading2'],
            fontSize=16,  # Increased from 12
            spaceAfter=15,
            alignment=1,
            fontName='Helvetica-Bold'
        )
        
        header_style = ParagraphStyle(
            'HeaderStyle',
            parent=styles['Normal'],
            fontSize=11,  # Increased from 9
            spaceAfter=5,
            alignment=1,
            textColor=colors.gray
        )
        
        # Table cell styles with word wrapping and larger fonts
        table_header_style = ParagraphStyle(
            'TableHeader',
            parent=styles['Normal'],
            fontSize=10,  # Increased from 8
            fontName='Helvetica-Bold',
            textColor=colors.white,
            alignment=1,
            wordWrap='CJK'  # Enable word wrapping
        )
        
        table_cell_style = ParagraphStyle(
            'TableCell',
            parent=styles['Normal'],
            fontSize=9,  # Increased from 7
            fontName='Helvetica',
            textColor=colors.black,
            alignment=0,  # Left align
            wordWrap='CJK',  # Enable word wrapping
            leading=10,  # Increased line spacing
        )
        
        # Create PDF content
        # 1. University Header
        header_data = template.get_header_data(timetable_type="Feedback Report")
        
        # Add logo if available
        try:
            if template.university_logo and hasattr(template.university_logo, 'path'):
                logo = Image(template.university_logo.path, width=1.5*inch, height=1.5*inch)  # Increased size
                logo.hAlign = 'CENTER'
                elements.append(logo)
        except (OSError, ValueError) as e:
            logger.warning("Could not load university logo for feedback report: %s", e)
        
        # University Name
        elements.append(Paragraph(header_data['university_name'], title_style))
        elements.append(Spacer(1, 10))
        
        # Directorate
        elements.append(Paragraph(header_data['directorate_name'], subtitle_style))
        elements.append(Spacer(1, 6))
        
        # Contact Information
        contact_info = f"{header_data['address']} | Tel: {header_data['telephone']} | Email: {header_data['email']} | Website: {header_data['website']}"
        elements.append(Paragraph(contact_info, header_style))
        elements.append(Spacer(1, 6))
        
        # Motto
        motto = f"{header_data['motto_latin']} | {header_data['motto_swahili']}"
        elements.append(Paragraph(motto, header_style))
        elements.append(Spacer(1, 25))
        
        # Report Title
        report_title = f"FEEDBACK REPORT - {timezone.now().strftime('%Y-%m-%d %H:%M')}"
        elements.append(Paragraph(report_title, subtitle_style))
        elements.append(Spacer(1, 15))
        
        # Filter Summary with larger font
        filter_style = ParagraphStyle(
            'FilterStyle',
            parent=styles['Normal'],
            fontSize=11,  # Increased font
            spaceAfter=15,
            alignment=0
        )
        
        filter_summary = f"<b>Total Feedbacks:</b> {feedbacks.count()}"
        if selected_years:
            filter_summary += f" | <b>Years:</b> {', '.join(selected_years)}"
        if selected_months:
            months = [datetime(2000, int(m), 1).strftime('%B') for m in selected_months]
            filter_summary += f" | <b>Months:</b> {', '.join(months)}"
        if date_range:
            filter_summary += f" | <b>Date Range:</b> {date_range.replace('_', ' ').title()}"
        if search_query:
            filter_summary += f" | <b>Search:</b> '{search_query[:30]}...'"
        
        elements.append(Paragraph(filter_summary, filter_style))
        elements.append(Spacer(1, 15))
        
        # Create table data with wrapped text
        table_data = []
        
        # Table headers as Paragraph objects for proper styling
        headers = ['No.', 'Full Name', 'Email', 'Admission No.', 'Message', 'Date', 'Status']
        header_cells = [Paragraph(header, table_header_style) for header in headers]
        table_data.append(header_cells)
        
        # Add feedback rows with wrapped text
        for i, feedback in enumerate(feedbacks, 1):
            # Truncate and wrap each field - increased character limits
            full_name = str(feedback.full_name)[:40] + '...' if len(str(feedback.full_name)) > 40 else str(feedback.full_name)
            email = str(feedback.email)[:35] + '...' if len(str(feedback.email)) > 35 else str(feedback.email)
            admission_no = str(feedback.admission_number or 'N/A')[:20] if feedback.admission_number else 'N/A'
            
            # Display full message in table (it will wrap)
            message_text = str(feedback.message)
            
            # Determine status text for PDF
            if feedback.status == 'solved':
                status_text = 'Solved'
            elif feedback.status == 'attended':
                status_text = 'Attended'
            elif feedback.seen:
                status_text = 'Seen'
            else:
                status_text = 'Unseen'
            
            # Create wrapped cells as Paragraph objects
            row = [
                Paragraph(str(i), table_cell_style),  # No.
                Paragraph(full_name, table_cell_style),  # Name
                Paragraph(email, table_cell_style),  # Email
                Paragraph(admission_no, table_cell_style),  # Admission No.
                Paragraph(message_text, table_cell_style),  # Full Message
                Paragraph(feedback.created_at.strftime('%Y-%m-%d\n%H:%M'), table_cell_style),  # Date (split on newline)
                Paragraph(status_text, table_cell_style),  # Status
            ]
            table_data.append(row)
        
        # Calculate column widths based on A4 page width (595 points)
        # Adjusted for larger fonts and full messages
        available_width = 595 - 72  # A4 width minus margins
        
        # Optimized column widths (in points) - adjusted for better display
        col_widths = [
            25,   # No. (0.35 inch)
            90,   # Full Name (1.25 inch)
            100,  # Email (1.39 inch)
            60,   # Admission No. (0.83 inch)
            200,  # Message (2.78 inch) - wider for full messages
            70,   # Date (0.97 inch)
            50,   # Status (0.69 inch)
        ]
        
        # Adjust if total width exceeds available space
        total_width = sum(col_widths)
        if total_width > available_width:
            # Reduce message column proportionally
            reduction_ratio = available_width / total_width
            col_widths = [int(width * reduction_ratio) for width in col_widths]
        
        # Create table with word wrapping enabled
        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        
        # Apply table styles with proper padding
        table_style = TableStyle([
            # Header style
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d47a1')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            
            # Cell alignment
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),  # Center align numbers
            ('ALIGN', (3, 1), (3, -1), 'CENTER'),  # Center align admission numbers
            ('ALIGN', (-1, 1), (-1, -1), 'CENTER'),  # Center align status
            
            # Grid and borders
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            
            # Alternating row colors for better readability
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
            
            # Cell padding for better text wrapping
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            
            # Vertical alignment
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),  # Align to top for multi-line messages
            
            # Word wrap for all cells
            ('WORDWRAP', (0, 0), (-1, -1), True),
        ])
        
        table.setStyle(table_style)
        elements.append(table)
        elements.append(Spacer(1, 30))
        
        # Add detailed messages section for all feedbacks (full messages)
        if feedbacks.count() <= 20:  # Show detailed section for up to 20 feedbacks
            elements.append(Paragraph("<b>Detailed Feedback Messages:</b>", styles['Heading2']))
            elements.append(Spacer(1, 15))
            
            for i, feedback in enumerate(feedbacks, 1):
                # Create a clean box for each detailed message with full content
                details_box = f"""
                <b>{i}. {feedback.full_name}</b><br/>
                <b>Email:</b> {feedback.email}<br/>
                <b>Admission No:</b> {feedback.admission_number or 'N/A'}<br/>
                <b>Date:</b> {feedback.created_at.strftime('%Y-%m-%d %H:%M')}<br/>
                <b>Status:</b> {feedback.get_status_display()}<br/>
                <br/>
                <b>Message:</b><br/>
                {feedback.message}
                """
                
                # Create a styled paragraph for the message box with larger font
                message_box_style = ParagraphStyle(
                    'MessageBox',
                    parent=styles['Normal'],
                    fontSize=10,  # Increased font
                    leftIndent=15,
                    rightIndent=15,
                    spaceBefore=12,
                    spaceAfter=12,
                    borderWidth=1,
                    borderColor=colors.grey,
                    borderRadius=3,
                    backColor=colors.HexColor('#f8f9fa'),
                    wordWrap='CJK',
                    leading=12,
                )
                
                elements.append(Paragraph(details_box, message_box_style))
                elements.append(Spacer(1, 20))
                
                # Add page break if we have many feedbacks
                if i % 5 == 0 and i < len(feedbacks):
                    elements.append(PageBreak())
                    # Add header again on new page
                    elements.append(Paragraph(report_title, subtitle_style))
                    elements.append(Spacer(1, 10))
        
        # Add footer with signatures and date
        elements.append(Spacer(1, 40))
        
        # Get footer data
        footer_data = template.get_footer_data(
            prepared_by="Feedback Management System",
            director_initials="DIR",
            sub_director_initials="EXT"
        )
        
        # Signature lines with proper spacing and larger font
        signature_style = ParagraphStyle(
            'Signature',
            parent=styles['Normal'],
            fontSize=12,  # Increased font
            spaceAfter=10,
            alignment=0,
            fontName='Helvetica'
        )
        
        # Current date for signatures
        current_date = timezone.now().strftime("%Y-%m-%d")
        
        # System signature section
        elements.append(Paragraph("<b>System Generated:</b>", signature_style))
        elements.append(Paragraph("_________________________", signature_style))
        elements.append(Paragraph(f"Generated by: Feedback Management System", signature_style))
        elements.append(Paragraph(f"Date: {current_date}", signature_style))
        elements.append(Paragraph(f"Time: {timezone.now().strftime('%H:%M:%S')}", signature_style))
        elements.append(Paragraph(f"Report ID: FB-{timezone.now().strftime('%Y%m%d-%H%M%S')}", signature_style))
        
        elements.append(Spacer(1, 30))
        
        # Director signature section
        elements.append(Paragraph("<b>Director's Signature:</b>", signature_style))
        elements.append(Paragraph("_________________________", signature_style))
        elements.append(Paragraph(footer_data['director_label'], signature_style))
        elements.append(Paragraph(f"Signature: {footer_data.get('signature', 'DIR/EXT')}", signature_style))
        elements.append(Paragraph(f"Date: {current_date}", signature_style))
        
        elements.append(Spacer(1, 30))
        
        # Prepared by section
        elements.append(Paragraph("<b>Prepared By:</b>", signature_style))
        elements.append(Paragraph("_________________________", signature_style))
        elements.append(Paragraph(f"Name: {footer_data.get('prepared_by', 'Feedback System Operator')}", signature_style))
        elements.append(Paragraph(f"Date: {current_date}", signature_style))
        
        # Add notes with larger font - FIXED: Use Helvetica instead of Helvetica-Italic
        elements.append(Spacer(1, 40))
        notes_style = ParagraphStyle(
            'Notes',
            parent=styles['Normal'],
            fontSize=10,  # Increased font
            textColor=colors.grey,
            alignment=0,
            fontName='Helvetica',  # FIXED: Changed from 'Helvetica-Italic'
            leftIndent=10,
            rightIndent=10,
            spaceBefore=10,
            spaceAfter=10,
            borderWidth=0.5,
            borderColor=colors.lightgrey,
            borderRadius=2,
            backColor=colors.HexColor('#fafafa'),
        )
        
        notes_text = f"""
        <b>Important Notes:</b><br/>
        • {footer_data['notes']}<br/>
        • This report was automatically generated by the Feedback Management System.<br/>
        • For any discrepancies, please contact the Directorate of Examinations and Timetabling.<br/>
        • Report generated on: {timezone.now().strftime('%Y-%m-%d at %H:%M:%S')}<br/>
        • Total records in this report: {feedbacks.count()}
        """
        
        elements.append(Paragraph(notes_text, notes_style))
        
        # Build PDF
        doc.build(elements)
        
        # Get PDF value from buffer
        pdf = buffer.getvalue()
        buffer.close()
        
        # Create HTTP response with PDF
        response = HttpResponse(content_type='application/pdf')
        filename = f"feedbacks_report_{timezone.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response.write(pdf)

        # ── Log this action ───────────────────────────────────────────────────
        log_export_pdf(
            filters={
                "selected_years": selected_years,
                "selected_months": selected_months,
                "search_query": search_query,
                "date_range": date_range,
                "selected_ids": selected_ids,
            },
            record_count=feedbacks.count(),
            filename=filename,
            success=True,
        )

        return response

    except Exception as e:
        # Log error and return error response
        import traceback
        print(f"Error generating PDF: {e}")
        print(traceback.format_exc())

        log_export_pdf(
            filters={
                "selected_years": request.GET.getlist('years[]', []),
                "selected_months": request.GET.getlist('months[]', []),
                "search_query": request.GET.get('search', ''),
                "date_range": request.GET.get('date_range', ''),
                "selected_ids": request.GET.getlist('selected_ids[]', []),
            },
            record_count=0,
            filename="",
            success=False,
            error=str(e),
        )

        return HttpResponse(
            f"Error generating PDF: {str(e)}",
            status=500,
            content_type='text/plain'
        )