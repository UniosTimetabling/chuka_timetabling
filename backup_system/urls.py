from django.urls import path
from . import views

app_name = 'backup_system'

urlpatterns = [
    # Backup Management
    path('', views.backup_dashboard, name='dashboard'),
    path('config/save/', views.create_or_update_config, name='save_config'),
    path('config/<int:config_id>/destination/', views.configure_destination, name='configure_destination'),
    path('destination/<int:destination_id>/test/', views.test_destination, name='test_destination'),
    path('config/<int:config_id>/run-now/', views.run_backup_now, name='run_backup_now'),

    # Backup History
    path('history/', views.backup_history, name='history'),
    path('history/<int:job_id>/download/', views.download_backup, name='download_backup'),
    path('history/<int:job_id>/verify/', views.verify_backup, name='verify_backup'),
    path('history/<int:job_id>/delete/', views.delete_backup, name='delete_backup'),
    path('history/<int:job_id>/restore/', views.restore_backup, name='restore_backup'),

    # Disaster Recovery
    path('disaster-recovery/', views.dr_dashboard, name='dr_dashboard'),
    path('disaster-recovery/configure/', views.configure_dr, name='configure_dr'),
    path('disaster-recovery/health-check/', views.check_replica_health_now, name='check_replica_health'),
    path('disaster-recovery/failover/', views.trigger_manual_failover, name='trigger_failover'),

    # Audit Trail / Undo
    path('audit-log/', views.audit_log_list, name='audit_log'),
    path('undo/<int:audit_log_id>/single/', views.undo_single, name='undo_single'),
    path('undo/multiple/', views.undo_multiple, name='undo_multiple'),
    path('undo/time-range/', views.undo_time_range, name='undo_time_range'),
    path('undo/history/', views.undo_history, name='undo_history'),
]
