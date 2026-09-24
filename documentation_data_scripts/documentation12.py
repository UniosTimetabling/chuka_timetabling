#!/usr/bin/env python
"""
SYSTEM DESIGN DOCUMENTATION FOR UNIVERSITY TIMETABLING SYSTEM
This script creates comprehensive system design documentation including:
- Entity Relationship Diagrams (ERD)
- System Architecture Stack
- Database Design Documentation
- File Management Structure
- Process Flows and Workflows
- System Components and Integration

Run: python manage.py shell < this_script.py
"""

import os
import django
import sys
import json
from datetime import datetime

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project.settings')
django.setup()

from documentation.models import (
    DocumentationCategory, DocumentationPage, DocumentationSection, 
    CodeExample, DocumentationTag, PageTag, InternalLink, DocumentationImage
)
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.files.base import ContentFile

print("=" * 80)
print("SYSTEM DESIGN DOCUMENTATION - UNIVERSITY TIMETABLING SYSTEM")
print("=" * 80)

# Get admin user for documentation
try:
    author = User.objects.filter(is_superuser=True).first()
    if not author:
        print("ERROR: No admin user found. Please create an admin user first.")
        sys.exit(1)
    print(f"Using admin: {author.username}")
except Exception as e:
    print(f"Error with user: {e}")
    sys.exit(1)

# ============================================================
# 1. CREATE SYSTEM DESIGN CATEGORIES
# ============================================================

print("\n1. CREATING SYSTEM DESIGN CATEGORIES...")

categories_data = [
    {
        'name': 'System Architecture',
        'slug': 'system-architecture',
        'description': 'Overall system architecture, technology stack, and deployment',
        'icon': 'fas fa-sitemap',
        'order': 40,
        'access_level': 'admin',
    },
    {
        'name': 'Database Design',
        'slug': 'database-design',
        'description': 'Database schema, relationships, and optimization',
        'icon': 'fas fa-database',
        'order': 41,
        'access_level': 'admin',
    },
    {
        'name': 'Entity Relationship Diagrams',
        'slug': 'entity-relationship-diagrams',
        'description': 'ERD diagrams and entity relationships',
        'icon': 'fas fa-project-diagram',
        'order': 42,
        'access_level': 'management',
    },
    {
        'name': 'File Management System',
        'slug': 'file-management-system',
        'description': 'File structure, uploads, and media management',
        'icon': 'fas fa-folder-tree',
        'order': 43,
        'access_level': 'admin',
    },
    {
        'name': 'Process Flows',
        'slug': 'process-flows',
        'description': 'System workflows and business processes',
        'icon': 'fas fa-stream',
        'order': 44,
        'access_level': 'management',
    },
    {
        'name': 'System Components',
        'slug': 'system-components',
        'description': 'Detailed component breakdown and interactions',
        'icon': 'fas fa-cogs',
        'order': 45,
        'access_level': 'admin',
    },
    {
        'name': 'Security Architecture',
        'slug': 'security-architecture',
        'description': 'Security design, authentication, and authorization',
        'icon': 'fas fa-shield-alt',
        'order': 46,
        'access_level': 'admin',
    },
    {
        'name': 'API Documentation',
        'slug': 'api-documentation',
        'description': 'REST API endpoints and integration',
        'icon': 'fas fa-code',
        'order': 47,
        'access_level': 'management',
    },
]

categories = {}
for cat_data in categories_data:
    category, created = DocumentationCategory.objects.get_or_create(
        slug=cat_data['slug'],
        defaults=cat_data
    )
    
    if not created:
        updated = False
        for field, value in cat_data.items():
            if getattr(category, field) != value:
                setattr(category, field, value)
                updated = True
        if updated:
            category.save()
            print(f"   Updated: {category.name}")
    else:
        print(f"   Created: {category.name}")
    
    categories[cat_data['slug']] = category

# ============================================================
# 2. CREATE SYSTEM DESIGN DOCUMENTATION PAGES
# ============================================================

print("\n2. CREATING SYSTEM DESIGN DOCUMENTATION PAGES...")

# ============================================================
# PAGE 1: System Architecture Overview
# ============================================================

page1_content = """
<h2>System Architecture Overview</h2>
<p>Complete architecture design of the University Timetabling System.</p>

<h3>Architecture Diagram</h3>
<div class="architecture-diagram">
<pre>
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                │
│  │   Students  │  │  Lecturers  │  │   Admins    │                │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                │
│         │                │                │                       │
│         └────────────────┼────────────────┘                       │
│                          ▼                                        │
│               ┌──────────────────────┐                            │
│               │   Web Browser        │                            │
│               │  (HTML/CSS/JS)       │                            │
│               └──────────┬───────────┘                            │
│                          │                                        │
└──────────────────────────┼────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         PRESENTATION LAYER                          │
│               ┌──────────────────────┐                            │
│               │   Django Templates   │                            │
│               │     Bootstrap 5      │                            │
│               │     JavaScript       │                            │
│               └──────────┬───────────┘                            │
│                          │                                        │
└──────────────────────────┼────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         APPLICATION LAYER                           │
│               ┌──────────────────────┐                            │
│               │    Django Views      │                            │
│               │     (Python)         │                            │
│               │  • Business Logic    │                            │
│               │  • Authentication    │                            │
│               │  • Authorization     │                            │
│               │  • API Endpoints     │                            │
│               └──────────┬───────────┘                            │
│                          │                                        │
└──────────────────────────┼────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         DATA ACCESS LAYER                           │
│               ┌──────────────────────┐                            │
│               │   Django ORM         │                            │
│               │   (Models.py)        │                            │
│               │  • Data Models       │                            │
│               │  • Relationships     │                            │
│               │  • Migrations        │                            │
│               └──────────┬───────────┘                            │
│                          │                                        │
└──────────────────────────┼────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         DATA STORAGE LAYER                          │
│               ┌──────────────────────┐                            │
│               │   PostgreSQL DB      │                            │
│               │  • Structured Data   │                            │
│               │  • Relations         │                            │
│               └──────────┬───────────┘                            │
│                          │                                        │
│               ┌──────────┴───────────┐                            │
│               │   Redis Cache        │                            │
│               │  • Session Storage   │                            │
│               │  • Caching           │                            │
│               └──────────┬───────────┘                            │
│                          │                                        │
│               ┌──────────┴───────────┐                            │
│               │   File System        │                            │
│               │  • Media Files       │                            │
│               │  • Static Files      │                            │
│               └──────────────────────┘                            │
└─────────────────────────────────────────────────────────────────────┘
</pre>
</div>

<h3>Technology Stack</h3>
<h4>Backend Technologies</h4>
<table class="table table-bordered">
<thead><tr><th>Technology</th><th>Version</th><th>Purpose</th><th>Key Features Used</th></tr></thead>
<tbody>
<tr><td><strong>Django</strong></td><td>4.2+</td><td>Web Framework</td><td>• ORM • Authentication • Admin • Forms • Templates</td></tr>
<tr><td><strong>Python</strong></td><td>3.9+</td><td>Programming Language</td><td>• Core Logic • Data Processing • API Development</td></tr>
<tr><td><strong>PostgreSQL</strong></td><td>14+</td><td>Database</td><td>• ACID Compliance • JSON Support • Full-text Search</td></tr>
<tr><td><strong>Redis</strong></td><td>7.0+</td><td>Caching & Sessions</td><td>• Session Storage • Cache Backend • Celery Broker</td></tr>
<tr><td><strong>Celery</strong></td><td>5.3+</td><td>Task Queue</td><td>• Background Tasks • Email Notifications • Report Generation</td></tr>
</tbody>
</table>

<h4>Frontend Technologies</h4>
<table class="table table-bordered">
<thead><tr><th>Technology</th><th>Version</th><th>Purpose</th><th>Key Features Used</th></tr></thead>
<tbody>
<tr><td><strong>HTML5</strong></td><td>-</td><td>Markup</td><td>• Semantic Elements • Form Validation • Accessibility</td></tr>
<tr><td><strong>CSS3</strong></td><td>-</td><td>Styling</td><td>• Flexbox • Grid • Custom Properties</td></tr>
<tr><td><strong>JavaScript</strong></td><td>ES6+</td><td>Client-side Logic</td><td>• AJAX • DOM Manipulation • Event Handling</td></tr>
<tr><td><strong>Bootstrap</strong></td><td>5.3</td><td>CSS Framework</td><td>• Responsive Grid • Components • Utilities</td></tr>
<tr><td><strong>jQuery</strong></td><td>3.6+</td><td>DOM Manipulation</td><td>• AJAX Calls • Event Binding • Animations</td></tr>
<tr><td><strong>Chart.js</strong></td><td>4.0+</td><td>Data Visualization</td><td>• Timetable Charts • Statistics • Reports</td></tr>
</tbody>
</table>

<h4>Development Tools</h4>
<table class="table table-bordered">
<thead><tr><th>Tool</th><th>Purpose</th><th>Configuration</th></tr></thead>
<tbody>
<tr><td><strong>Git</strong></td><td>Version Control</td><td>• Git Flow • Feature Branches • PR Reviews</td></tr>
<tr><td><strong>Docker</strong></td><td>Containerization</td><td>• Development • Testing • Production</td></tr>
<tr><td><strong>GitHub Actions</strong></td><td>CI/CD</td><td>• Automated Testing • Deployment • Security Scans</td></tr>
<tr><td><strong>Postman</strong></td><td>API Testing</td><td>• Collection Testing • Documentation • Mock Servers</td></tr>
<tr><td><strong>VS Code</strong></td><td>IDE</td><td>• Python Support • Django Extensions • Debugging</td></tr>
</tbody>
</table>

<h3>Deployment Architecture</h3>
<h4>Production Environment</h4>
<pre>
┌─────────────────────────────────────────────────────────────────────┐
│                    LOAD BALANCER (Nginx)                            │
│                            │                                         │
│                            ▼                                         │
│              ┌─────────────────────────────┐                       │
│              │        Web Servers          │                       │
│              │   (Gunicorn/Uvicorn)        │                       │
│              │  • App Instance 1           │                       │
│              │  • App Instance 2           │                       │
│              │  • App Instance 3           │                       │
│              └─────────────┬───────────────┘                       │
│                            │                                         │
│              ┌─────────────┴───────────────┐                       │
│              │      Background Workers      │                       │
│              │        (Celery)              │                       │
│              │  • Email Tasks               │                       │
│              │  • Report Generation         │                       │
│              │  • Data Processing           │                       │
│              └─────────────┬───────────────┘                       │
│                            │                                         │
└────────────────────────────┼─────────────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │      Shared Services        │
              ├─────────────────────────────┤
              │  PostgreSQL (Primary)       │
              │  PostgreSQL (Replica)       │
              │  Redis (Cache & Sessions)   │
              │  Redis (Celery Broker)      │
              │  MinIO/S3 (Media Storage)   │
              └─────────────────────────────┘
</pre>

<h4>Scaling Strategy</h4>
<ol>
<li><strong>Horizontal Scaling</strong>: Multiple Django instances behind load balancer</li>
<li><strong>Database Scaling</strong>: Read replicas for reporting and analytics</li>
<li><strong>Cache Strategy</strong>: Redis cluster for session and cache distribution</li>
<li><strong>Static Files</strong>: CDN for global static file delivery</li>
<li><strong>Media Files</strong>: Object storage with lifecycle policies</li>
</ol>

<h3>System Requirements</h3>
<h4>Hardware Requirements</h4>
<table class="table table-bordered">
<thead><tr><th>Component</th><th>Development</th><th>Staging</th><th>Production</th></tr></thead>
<tbody>
<tr><td><strong>CPU</strong></td><td>2 Cores</td><td>4 Cores</td><td>8+ Cores</td></tr>
<tr><td><strong>RAM</strong></td><td>4GB</td><td>8GB</td><td>16GB+</td></tr>
<tr><td><strong>Storage</strong></td><td>20GB</td><td>50GB</td><td>100GB+</td></tr>
<tr><td><strong>Network</strong></td><td>100Mbps</td><td>1Gbps</td><td>1Gbps+</td></tr>
</tbody>
</table>

<h4>Software Requirements</h4>
<ul>
<li><strong>Operating System</strong>: Ubuntu 20.04 LTS or later / CentOS 7+</li>
<li><strong>Python</strong>: 3.9 or later with pip and virtualenv</li>
<li><strong>Database</strong>: PostgreSQL 14+ with PostGIS extension</li>
<li><strong>Cache</strong>: Redis 7.0+</li>
<li><strong>Web Server</strong>: Nginx 1.18+ or Apache 2.4+</li>
<li><strong>WSGI Server</strong>: Gunicorn 20.0+ or Uvicorn 0.20+</li>
</ul>

<h3>Development Environment Setup</h3>
<h4>Local Development</h4>
<pre><code class="bash"># Clone the repository
git clone https://github.com/your-org/university-timetabling.git
cd university-timetabling

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\\Scripts\\activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Configure environment variables
cp .env.example .env
# Edit .env with your configuration

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run development server
python manage.py runserver</code></pre>

<h4>Docker Development</h4>
<pre><code class="dockerfile"># docker-compose.yml
version: '3.8'

services:
  db:
    image: postgres:14
    environment:
      POSTGRES_DB: timetabling
      POSTGRES_USER: timetabling_user
      POSTGRES_PASSWORD: secure_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
  
  redis:
    image: redis:7-alpine
  
  web:
    build: .
    command: python manage.py runserver 0.0.0.0:8000
    volumes:
      - .:/app
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgres://timetabling_user:secure_password@db:5432/timetabling
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis
  
  celery:
    build: .
    command: celery -A your_project worker -l info
    volumes:
      - .:/app
    environment:
      - DATABASE_URL=postgres://timetabling_user:secure_password@db:5432/timetabling
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis

volumes:
  postgres_data:</code></pre>

<h3>Monitoring and Logging</h3>
<h4>Application Monitoring</h4>
<ul>
<li><strong>Application Performance</strong>: Django Debug Toolbar, Silk</li>
<li><strong>Error Tracking</strong>: Sentry, Rollbar</li>
<li><strong>Logging</strong>: Structured logging with JSON format</li>
<li><strong>Metrics</strong>: Prometheus metrics with Grafana dashboards</li>
</ul>

<h4>Logging Configuration</h4>
<pre><code class="python"># settings.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '''
                %(levelname)s %(asctime)s %(module)s 
                %(process)d %(thread)d %(message)s
            '''
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': '/var/log/timetabling/app.log',
            'maxBytes': 1024 * 1024 * 10,  # 10 MB
            'backupCount': 10,
            'formatter': 'json',
        },
        'error_file': {
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': '/var/log/timetabling/error.log',
            'maxBytes': 1024 * 1024 * 10,
            'backupCount': 10,
            'formatter': 'json',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': True,
        },
        'TT_APP': {
            'handlers': ['console', 'file', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}</code></pre>

<h3>Backup and Recovery</h3>
<h4>Backup Strategy</h4>
<ol>
<li><strong>Database Backups</strong>: Daily full backups with hourly transaction logs</li>
<li><strong>File Backups</strong>: Daily incremental media file backups</li>
<li><strong>Configuration Backups</strong>: Version-controlled in Git</li>
<li><strong>Retention Policy</strong>: 30 days for daily, 12 months for monthly</li>
</ol>

<h4>Recovery Procedures</h4>
<pre><code class="bash"># Database recovery example
# Restore from backup
pg_restore -d timetabling_db /backups/db_backup.dump

# Media files recovery
aws s3 sync s3://timetabling-media-backup/ /media/</code></pre>

<h3>Security Considerations</h3>
<h4>Security Measures</h4>
<ul>
<li><strong>HTTPS</strong>: All traffic encrypted with TLS 1.3</li>
<li><strong>Security Headers</strong>: CSP, HSTS, X-Frame-Options</li>
<li><strong>Authentication</strong>: Django's built-in auth with password policies</li>
<li><strong>Authorization</strong>: Role-based access control (RBAC)</li>
<li><strong>Data Encryption</strong>: At-rest encryption for database and files</li>
<li><strong>Audit Logging</strong>: All sensitive operations logged</li>
</ul>

<h4>Security Headers Configuration</h4>
<pre><code class="python"># Django Security Middleware
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = 'DENY'</code></pre>
"""

page1 = DocumentationPage.objects.create(
    title='System Architecture - Complete Overview',
    slug='system-architecture-complete-overview',
    short_description='Complete system architecture including technology stack, deployment, and infrastructure',
    content=page1_content,
    category=categories['system-architecture'],
    page_type='reference',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=30,
    version='1.0'
)
print(f"   Created: {page1.title}")

# ============================================================
# PAGE 2: Database Design Documentation
# ============================================================

page2_content = """
<h2>Database Design Documentation</h2>
<p>Comprehensive database schema design for the University Timetabling System.</p>

<h3>Database Schema Overview</h3>
<div class="erd-diagram">
<pre>
┌─────────────────────────────────────────────────────────────────────────┐
│                    DATABASE SCHEMA - CORE ENTITIES                      │
│                                                                         │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐              │
│  │   Faculty   │1───∞│ Department  │1───∞│   Program   │              │
│  └─────────────┘     └──────┬──────┘     └──────┬──────┘              │
│                              │                   │                     │
│                    ┌─────────┴─────────┐ ┌──────┴────────┐            │
│                    │                   │ │               │            │
│             ┌─────────────┐     ┌─────────────┐   ┌─────────────┐    │
│             │   Course    │∞───∞│ ProgramCourse│∞──│CourseAllocation│  │
│             │  Allocation │     └─────────────┘   └──────┬──────┘    │
│             └──────┬──────┘                              │            │
│                    │                              ┌──────┴────────┐    │
│             ┌──────┴──────┐                ┌─────────────┐ ┌─────────────┐
│             │   Lecturer  │1───∞           │  Timetable  │ │ ExamTimetable│
│             └─────────────┘                └─────────────┘ └─────────────┘
│                    │1                                            │1       │
│             ┌──────┴──────┐                                ┌─────┴─────┐  │
│             │     User    │∞──────────────────────────────∞│   Group   │  │
│             └─────────────┘                                └───────────┘  │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
</pre>
</div>

<h3>Core Database Models</h3>

<h4>1. Academic Structure Models</h4>
<pre><code class="python"># Faculty Model
class Faculty(models.Model):
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True, null=True)
    leader = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Indexes: name for quick lookup
    class Meta:
        indexes = [models.Index(fields=['name'])]
        verbose_name_plural = "Faculties"

# Department Model
class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)
    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name="departments")
    description = models.TextField(blank=True, null=True)
    leader = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Indexes: name and faculty for common queries
    class Meta:
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['faculty', 'name']),
        ]

# Program Model
class Program(models.Model):
    name = models.CharField(max_length=200, unique=True)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="programs")
    description = models.TextField(blank=True, null=True)
    
    # Indexes: name and department for program lookup
    class Meta:
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['department', 'name']),
        ]
        ordering = ['name']</code></pre>

<h4>2. Course Management Models</h4>
<pre><code class="python"># ProgramCourse Model
class ProgramCourse(models.Model):
    SEMESTER_CHOICES = [(1, "Semester 1"), (2, "Semester 2")]
    YEAR_CHOICES = [(i, f"Year {i}") for i in range(1, 7)]
    
    program = models.ForeignKey('Program', on_delete=models.CASCADE, related_name="courses")
    course_code = models.CharField(max_length=20)
    course_name = models.CharField(max_length=200)
    year = models.PositiveSmallIntegerField(choices=YEAR_CHOICES, default=1)
    semester = models.PositiveSmallIntegerField(choices=SEMESTER_CHOICES, default=1)
    
    # Unique constraint: course per program/year/semester
    class Meta:
        unique_together = ("program", "course_code", "year", "semester")
        indexes = [
            models.Index(fields=['program', 'year', 'semester']),
            models.Index(fields=['course_code']),
        ]

# CourseAllocation Model
class CourseAllocation(models.Model):
    course_code = models.CharField(max_length=20)
    course_name = models.CharField(max_length=200)
    department = models.ForeignKey("Department", on_delete=models.CASCADE, related_name="allocations")
    origin_department = models.ForeignKey("Department", on_delete=models.SET_NULL, null=True, blank=True)
    program = models.ForeignKey("Program", on_delete=models.CASCADE, related_name="allocations", null=True, blank=True)
    lecturer = models.ForeignKey("Lecturer", on_delete=models.SET_NULL, null=True, blank=True)
    number_of_students = models.PositiveIntegerField(default=0)
    
    # Approval workflow fields
    approved_by_dvc = models.BooleanField(default=False)
    rejected_by_dvc = models.BooleanField(default=False)
    reason_for_disapproval = models.TextField(blank=True, default="No reason yet")
    submitted_to_tt = models.BooleanField(default=False)
    
    # Indexes for common queries
    class Meta:
        indexes = [
            models.Index(fields=['department', 'submitted_to_tt']),
            models.Index(fields=['program', 'course_code']),
            models.Index(fields=['lecturer']),
            models.Index(fields=['approved_by_dvc', 'rejected_by_dvc']),
        ]</code></pre>

<h4>3. Timetable Models</h4>
<pre><code class="python"># Timetable Model (Main Class Schedule)
class Timetable(models.Model):
    course_allocation = models.ForeignKey(CourseAllocation, on_delete=models.CASCADE, related_name="timetable_entries")
    venue = models.CharField(max_length=100)
    day = models.CharField(max_length=20)  # Monday, Tuesday, etc.
    start_time = models.TimeField()
    end_time = models.TimeField()
    
    # Indexes for timetable queries
    class Meta:
        indexes = [
            models.Index(fields=['day', 'start_time']),
            models.Index(fields=['venue', 'day']),
            models.Index(fields=['course_allocation']),
        ]
        ordering = ['day', 'start_time']

# ExamTimetable Model
class ExamTimetable(models.Model):
    course_allocation = models.ForeignKey("CourseAllocation", on_delete=models.CASCADE, related_name="exam_timetable_entries")
    venue = models.CharField(max_length=100)
    day = models.CharField(max_length=20)
    date = models.DateField(default=timezone.now)
    start_time = models.TimeField()
    end_time = models.TimeField()
    
    # Indexes for exam scheduling
    class Meta:
        indexes = [
            models.Index(fields=['date', 'start_time']),
            models.Index(fields=['venue', 'date']),
            models.Index(fields=['course_allocation']),
        ]
        ordering = ["date", "start_time"]</code></pre>

<h4>4. User and Role Models</h4>
<pre><code class="python"># User Model (extends Django's built-in User)
# Uses Django's built-in User model with custom profile

# Lecturer Model
class Lecturer(models.Model):
    DESIGNATIONS = [
        ("Prof", "Prof."),
        ("Dr", "Dr."),
        ("Mr", "Mr."),
        ("Ms", "Ms."),
        ("Mrs", "Mrs."),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True, related_name="lecturer_profile")
    payroll_number = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    email = models.EmailField(unique=True)
    designation = models.CharField(max_length=10, choices=DESIGNATIONS)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Indexes for lecturer lookups
    class Meta:
        indexes = [
            models.Index(fields=['payroll_number']),
            models.Index(fields=['email']),
            models.Index(fields=['department']),
        ]

# ClassRep Model
class ClassRep(models.Model):
    full_name = models.CharField(max_length=150, blank=True)
    reg_no = models.CharField(max_length=50, unique=True, blank=True)
    username = models.CharField(max_length=50, unique=True, blank=True)
    email = models.EmailField(unique=True, blank=True)
    password = models.CharField(max_length=128, default="123")
    program = models.ForeignKey("Program", on_delete=models.CASCADE, related_name="class_reps")
    date_registered = models.DateTimeField(default=timezone.now)
    active = models.BooleanField(default=True)
    
    # Indexes for class rep queries
    class Meta:
        indexes = [
            models.Index(fields=['username']),
            models.Index(fields=['reg_no']),
            models.Index(fields=['program', 'active']),
        ]</code></pre>

<h3>Database Relationships</h3>
<h4>One-to-One Relationships</h4>
<ul>
<li><code>User ↔ Lecturer</code>: One user can be one lecturer</li>
<li><code>User ↔ OrgRole</code>: One user has one organizational role</li>
<li><code>Department ↔ SubmissionControl</code>: One department has one submission control</li>
</ul>

<h4>One-to-Many Relationships</h4>
<ul>
<li><code>Faculty → Department</code>: One faculty has many departments</li>
<li><code>Department → Program</code>: One department has many programs</li>
<li><code>Program → ProgramCourse</code>: One program has many courses</li>
<li><code>Department → CourseAllocation</code>: One department has many course allocations</li>
<li><code>CourseAllocation → Timetable</code>: One course allocation has many timetable entries</li>
</ul>

<h4>Many-to-Many Relationships</h4>
<ul>
<li><code>CourseAllocation ↔ SharedVenueExamGroup</code>: Multiple courses can share exam venue</li>
<li><code>CourseAllocation ↔ MergedCourseGroup</code>: Multiple courses can be merged</li>
<li><code>CourseAllocation ↔ AutoMergedExamGroup</code>: Automatic merging of similar courses</li>
<li><code>User ↔ Group</code>: Django's built-in user-group relationship</li>
</ul>

<h3>Database Indexing Strategy</h3>
<h4>Primary Indexes</h4>
<pre><code class="sql">-- Core lookup indexes
CREATE INDEX idx_faculty_name ON TT_APP_faculty(name);
CREATE INDEX idx_department_faculty_name ON TT_APP_department(faculty_id, name);
CREATE INDEX idx_program_department_name ON TT_APP_program(department_id, name);

-- Course allocation indexes
CREATE INDEX idx_course_allocation_dept_submitted ON TT_APP_courseallocation(department_id, submitted_to_tt);
CREATE INDEX idx_course_allocation_program_code ON TT_APP_courseallocation(program_id, course_code);
CREATE INDEX idx_course_allocation_lecturer ON TT_APP_courseallocation(lecturer_id);

-- Timetable indexes
CREATE INDEX idx_timetable_day_time ON TT_APP_timetable(day, start_time);
CREATE INDEX idx_timetable_venue_day ON TT_APP_timetable(venue, day);
CREATE INDEX idx_exam_timetable_date_time ON TT_APP_examtimetable(date, start_time);</code></pre>

<h4>Composite Indexes</h4>
<pre><code class="sql">-- Frequently queried combinations
CREATE INDEX idx_program_course_program_year_semester 
ON TT_APP_programcourse(program_id, year, semester);

CREATE INDEX idx_course_allocation_status 
ON TT_APP_courseallocation(approved_by_dvc, rejected_by_dvc, submitted_to_tt);

CREATE INDEX idx_exam_temp_unique 
ON TT_APP_examtemptimetable(venue_id, date, start_time, end_time);</code></pre>

<h3>Database Constraints</h3>
<h4>Unique Constraints</h4>
<pre><code class="sql">-- ProgramCourse: unique per program/year/semester
ALTER TABLE TT_APP_programcourse 
ADD CONSTRAINT unique_program_course_per_semester 
UNIQUE (program_id, course_code, year, semester);

-- Timetable: prevent double booking of venues
ALTER TABLE TT_APP_timetable 
ADD CONSTRAINT unique_venue_time_slot 
UNIQUE (venue, day, start_time, end_time);

-- ExamTimetable: prevent exam venue double booking
ALTER TABLE TT_APP_examtimetable 
ADD CONSTRAINT unique_exam_venue_time 
UNIQUE (venue, date, start_time, end_time);</code></pre>

<h4>Check Constraints</h4>
<pre><code class="sql">-- CourseAllocation: cannot be both approved and rejected
ALTER TABLE TT_APP_courseallocation 
ADD CONSTRAINT check_not_both_approved_rejected 
CHECK (NOT (approved_by_dvc = true AND rejected_by_dvc = true));

-- ProgramCourse: year must be between 1 and 6
ALTER TABLE TT_APP_programcourse 
ADD CONSTRAINT check_year_range 
CHECK (year BETWEEN 1 AND 6);

-- Number of students must be positive
ALTER TABLE TT_APP_courseallocation 
ADD CONSTRAINT check_positive_students 
CHECK (number_of_students >= 0);</code></pre>

<h3>Database Views</h3>
<h4>Materialized Views for Reporting</h4>
<pre><code class="sql">-- Department Course Summary View
CREATE MATERIALIZED VIEW department_course_summary AS
SELECT 
    d.name as department_name,
    f.name as faculty_name,
    COUNT(DISTINCT ca.id) as total_courses,
    COUNT(DISTINCT CASE WHEN ca.approved_by_dvc THEN ca.id END) as approved_courses,
    COUNT(DISTINCT CASE WHEN ca.rejected_by_dvc THEN ca.id END) as rejected_courses,
    COUNT(DISTINCT CASE WHEN ca.submitted_to_tt THEN ca.id END) as submitted_courses,
    SUM(ca.number_of_students) as total_students
FROM TT_APP_department d
JOIN TT_APP_faculty f ON d.faculty_id = f.id
LEFT JOIN TT_APP_courseallocation ca ON d.id = ca.department_id
GROUP BY d.id, f.id, d.name, f.name;

-- Timetable Conflict Detection View
CREATE VIEW timetable_conflicts AS
SELECT 
    t1.id as timetable1_id,
    t2.id as timetable2_id,
    t1.venue,
    t1.day,
    t1.start_time,
    t1.end_time,
    t1.course_allocation_id as course1_id,
    t2.course_allocation_id as course2_id,
    CASE 
        WHEN t1.course_allocation_id = t2.course_allocation_id 
        THEN 'Duplicate Course'
        WHEN t1.venue = t2.venue 
        THEN 'Venue Conflict'
        ELSE 'Time Overlap'
    END as conflict_type
FROM TT_APP_timetable t1
JOIN TT_APP_timetable t2 ON 
    t1.day = t2.day AND
    t1.id < t2.id AND
    t1.start_time < t2.end_time AND
    t1.end_time > t2.start_time;</code></pre>

<h3>Database Performance Optimization</h3>
<h4>Query Optimization Techniques</h4>
<ol>
<li><strong>Select Related</strong>: Use select_related for foreign key relationships</li>
<li><strong>Prefetch Related</strong>: Use prefetch_related for many-to-many relationships</li>
<li><strong>Values/Values List</strong>: Use when only specific fields are needed</li>
<li><strong>Database Indexes</strong>: Strategic indexing based on query patterns</li>
<li><strong>Query Caching</strong>: Cache frequent queries with Redis</li>
</ol>

<h4>Example Optimized Query</h4>
<pre><code class="python"># Optimized timetable query
timetable_data = Timetable.objects.select_related(
    'course_allocation',
    'course_allocation__lecturer',
    'course_allocation__program'
).filter(
    course_allocation__program_id=program_id
).only(
    'venue', 'day', 'start_time', 'end_time',
    'course_allocation__course_code',
    'course_allocation__course_name',
    'course_allocation__lecturer__display_name'
).order_by('day', 'start_time')</code></pre>

<h3>Database Migration Strategy</h3>
<h4>Migration Best Practices</h4>
<pre><code class="python"># Example migration with data migration
from django.db import migrations, models
import django.db.models.deletion

def migrate_lecturer_data(apps, schema_editor):
    CourseAllocation = apps.get_model('TT_APP', 'CourseAllocation')
    Lecturer = apps.get_model('TT_APP', 'Lecturer')
    
    for allocation in CourseAllocation.objects.all():
        # Migration logic here
        pass

class Migration(migrations.Migration):
    dependencies = [
        ('TT_APP', 'previous_migration'),
    ]
    
    operations = [
        # Schema changes
        migrations.AddField(
            model_name='courseallocation',
            name='origin_department',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='origin_allocations',
                to='TT_APP.department',
            ),
        ),
        
        # Data migration
        migrations.RunPython(migrate_lecturer_data, reverse_code=migrations.RunPython.noop),
    ]</code></pre>

<h3>Database Backup and Recovery</h3>
<h4>Backup Procedures</h4>
<pre><code class="bash">#!/bin/bash
# Database backup script

BACKUP_DIR="/backups/database"
DATE=$(date +%Y%m%d_%H%M%S)
DB_NAME="timetabling_db"
DB_USER="timetabling_user"

# Create backup
pg_dump -U $DB_USER -Fc $DB_NAME > $BACKUP_DIR/backup_$DATE.dump

# Keep only last 30 days of backups
find $BACKUP_DIR -name "*.dump" -mtime +30 -delete

# Sync to remote storage
aws s3 sync $BACKUP_DIR s3://timetabling-backups/database/</code></pre>

<h3>Database Monitoring</h3>
<h4>Key Metrics to Monitor</h4>
<ul>
<li><strong>Query Performance</strong>: Slow query logging and analysis</li>
<li><strong>Connection Pool</strong>: Active connections and connection pooling</li>
<li><strong>Index Usage</strong>: Index hit ratio and unused indexes</li>
<li><strong>Table Sizes</strong>: Growth trends and storage planning</li>
<li><strong>Replication Lag</strong>: For read replicas</li>
</ul>

<h4>Monitoring Queries</h4>
<pre><code class="sql">-- Active queries
SELECT pid, usename, query, state, now() - query_start as duration
FROM pg_stat_activity 
WHERE state != 'idle' 
ORDER BY duration DESC;

-- Table sizes
SELECT 
    schemaname as schema,
    tablename as table,
    pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) as total_size,
    pg_size_pretty(pg_relation_size(schemaname || '.' || tablename)) as table_size,
    pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename) - 
                  pg_relation_size(schemaname || '.' || tablename)) as index_size
FROM pg_tables 
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_total_relation_size(schemaname || '.' || tablename) DESC;</code></pre>
"""

page2 = DocumentationPage.objects.create(
    title='Database Design - Complete Schema Documentation',
    slug='database-design-complete-schema-documentation',
    short_description='Comprehensive database schema design with relationships, indexes, and optimization',
    content=page2_content,
    category=categories['database-design'],
    page_type='reference',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=35,
    version='1.0'
)
print(f"   Created: {page2.title}")

# ============================================================
# PAGE 3: Entity Relationship Diagrams
# ============================================================

page3_content = """
<h2>Entity Relationship Diagrams (ERD)</h2>
<p>Complete entity relationship diagrams for the University Timetabling System.</p>

<h3>Complete ERD Diagram</h3>
<div class="erd-diagram">
<pre>
┌────────────────────────────────────────────────────────────────────────────────────┐
│                 UNIVERSITY TIMETABLING SYSTEM - COMPLETE ERD                        │
│                                                                                    │
│  ┌─────────────┐       ┌─────────────┐       ┌─────────────┐                     │
│  │    User     │◄──┐   │    Group    │       │  OrgRole    │                     │
│  └──────┬──────┘   │   └─────────────┘       └──────┬──────┘                     │
│         │          │                                │                            │
│    ┌────┴────┐     │   ┌─────────────┐       ┌─────┴─────┐                      │
│    │ Lecturer│     └───│CotUserProfile│      │ Submission │                     │
│    └────┬────┘         └──────┬──────┘      │  Control   │                     │
│         │                    │               └────────────┘                     │
│    ┌────┴────┐         ┌─────┴──────┐               │                            │
│    │Department│◄────────│  Faculty   │               │                            │
│    └────┬────┘         └────────────┘               │                            │
│         │                                            │                            │
│    ┌────┴────┐         ┌─────────────┐       ┌─────┴─────┐                      │
│    │ Program │◄────────│ Building    │       │   Venue   │                     │
│    └────┬────┘         └──────┬──────┘       └─────┬─────┘                     │
│         │                    │                    │                            │
│    ┌────┴────┐         ┌─────┴──────┐       ┌─────┴─────┐                      │
│    │Program  │         │ LabVenue   │       │ LabAlloc  │                     │
│    │ Course  │         └──────┬──────┘       └─────┬─────┘                     │
│    └────┬────┘                │                    │                            │
│         │               ┌─────┴──────┐       ┌─────┴─────┐                      │
│    ┌────┴────┐         │ LabTimetable│       │LabExamTime│                     │
│    │Course   │◄────────└─────────────┘       │  table    │                     │
│    │Allocation│                               └───────────┘                     │
│    └────┬────┘                                                                  │
│         │                                                                       │
│  ┌──────┴──────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐  │
│  │  Timetable  │     │ ExamTimetable│     │ TempTimetable│    │ExamTempTime │  │
│  └─────────────┘     └──────┬──────┘     └─────────────┘     │  table      │  │
│                              │                                └──────┬──────┘  │
│                      ┌───────┴───────┐                              │         │
│                      │ SharedVenue   │                     ┌────────┴────────┐│
│                      │  ExamGroup    │                     │ AutoMergedExam ││
│                      └───────┬───────┘                     │     Group      ││
│                              │                            └──────┬──────┘  │
│                      ┌───────┴───────┐                          │         │
│                      │ MergedCourse  │                 ┌────────┴────────┐│
│                      │    Group      │                 │ ArchivedCourse ││
│                      └───────────────┘                 │   Allocation   ││
│                                                        └────────────────┘│
│                                                                          │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                │
│  │  ClassRep   │     │   Feedback  │     │  Notification│                │
│  └──────┬──────┘     └─────────────┘     └─────────────┘                │
│         │                                                                 │
│  ┌──────┴──────┐     ┌─────────────┐     ┌─────────────┐                │
│  │MinimalTime  │     │ ProgramCode │     │Timetable    │                │
│  │   table     │     └─────────────┘     │ Archive     │                │
│  └─────────────┘                         └─────────────┘                │
│                                                                          │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                │
│  │     Food    │     │  FoodImage  │     │ FoodOption  │                │
│  └──────┬──────┘     └─────────────┘     └─────────────┘                │
│         │                                                                 │
│  ┌──────┴──────┐                                                         │
│  │    Offer    │                                                         │
│  └─────────────┘                                                         │
└──────────────────────────────────────────────────────────────────────────┘
</pre>
</div>

<h3>Detailed Entity Relationships</h3>

<h4>1. Academic Hierarchy Relationships</h4>
<pre>
Faculty (1) ──── (∞) Department (1) ──── (∞) Program (1) ──── (∞) ProgramCourse
    │                    │                       │
    │                    │                       │
    ▼                    ▼                       ▼
  Leader              Leader                  ClassRep
 (User)              (User)                  (ClassRep)
</pre>

<h4>2. Course Allocation Relationships</h4>
<pre>
ProgramCourse (∞) ──── (∞) CourseAllocation (1) ──── (∞) Lecturer (0..1) ──── (1) User
       │                        │                          │
       │                        │                          │
       ▼                        ▼                          ▼
    Program                Department                  Department
                           OriginDept
</pre>

<h4>3. Timetable Relationships</h4>
<pre>
CourseAllocation (1) ──── (∞) Timetable
                         (∞) ExamTimetable
                         (∞) TempTimetable
                         (∞) ExamTempTimetable
</pre>

<h4>4. User Role Relationships</h4>
<pre>
User (1) ──── (1) Lecturer
     │
     ├─── (∞) Group (Django built-in)
     │
     ├─── (1) OrgRole (organizational role)
     │
     ├─── (1) CotUserProfile (COT profile)
     │
     └─── (∞) ClassRep (can be class rep for programs)
</pre>

<h3>Relationship Cardinalities</h3>
<table class="table table-bordered">
<thead><tr><th>Relationship</th><th>From Entity</th><th>To Entity</th><th>Cardinality</th><th>Description</th></tr></thead>
<tbody>
<tr><td>Faculty-Department</td><td>Faculty</td><td>Department</td><td>1:N</td><td>One faculty has many departments</td></tr>
<tr><td>Department-Program</td><td>Department</td><td>Program</td><td>1:N</td><td>One department has many programs</td></tr>
<tr><td>Program-ProgramCourse</td><td>Program</td><td>ProgramCourse</td><td>1:N</td><td>One program has many courses</td></tr>
<tr><td>ProgramCourse-CourseAllocation</td><td>ProgramCourse</td><td>CourseAllocation</td><td>M:N</td><td>Courses can be allocated multiple times</td></tr>
<tr><td>CourseAllocation-Lecturer</td><td>CourseAllocation</td><td>Lecturer</td><td>N:1</td><td>Many courses can be taught by one lecturer</td></tr>
<tr><td>Lecturer-User</td><td>Lecturer</td><td>User</td><td>1:1</td><td>One lecturer corresponds to one user</td></tr>
<tr><td>CourseAllocation-Timetable</td><td>CourseAllocation</td><td>Timetable</td><td>1:N</td><td>One course can have multiple timetable entries</td></tr>
<tr><td>User-Group</td><td>User</td><td>Group</td><td>M:N</td><td>Users can belong to multiple groups</td></tr>
<tr><td>Department-SubmissionControl</td><td>Department</td><td>SubmissionControl</td><td>1:1</td><td>Each department has one submission control</td></tr>
<tr><td>Program-ClassRep</td><td>Program</td><td>ClassRep</td><td>1:N</td><td>One program can have multiple class reps</td></tr>
</tbody>
</table>

<h3>Entity Attributes</h3>

<h4>Faculty Entity</h4>
<pre>
Faculty {
    id: Integer (PK)
    name: String (150) [UNIQUE]
    description: Text [NULL]
    leader_id: Integer (FK → User) [NULL]
    created_at: DateTime
    updated_at: DateTime
}
</pre>

<h4>Department Entity</h4>
<pre>
Department {
    id: Integer (PK)
    name: String (150) [UNIQUE]
    faculty_id: Integer (FK → Faculty) [NOT NULL]
    description: Text [NULL]
    leader_id: Integer (FK → User) [NULL]
    created_at: DateTime
    updated_at: DateTime
}
</pre>

<h4>CourseAllocation Entity</h4>
<pre>
CourseAllocation {
    id: Integer (PK)
    course_code: String (20)
    course_name: String (200)
    department_id: Integer (FK → Department) [NOT NULL]
    origin_department_id: Integer (FK → Department) [NULL]
    program_id: Integer (FK → Program) [NULL]
    lecturer_id: Integer (FK → Lecturer) [NULL]
    number_of_students: Integer [DEFAULT: 0]
    approved_by_dvc: Boolean [DEFAULT: false]
    rejected_by_dvc: Boolean [DEFAULT: false]
    reason_for_disapproval: Text [DEFAULT: "No reason yet"]
    submitted_to_tt: Boolean [DEFAULT: false]
    created_at: DateTime
    updated_at: DateTime
}
</pre>

<h3>Entity Lifecycle Diagrams</h3>

<h4>Course Allocation Lifecycle</h4>
<pre>
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Created   │────▶│ Submitted   │────▶│   Approved  │────▶│ Scheduled   │
│   by COD    │     │  to DVC     │     │   by DVC    │     │ in Timetable│
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
        │                   │                   │                   │
        ▼                   ▼                   ▼                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Draft     │     │ Under       │     │  Rejected   │     │  Archived   │
│   State     │     │ Review      │     │  by DVC     │     │  (Past Sem) │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
</pre>

<h4>Timetable Entry Lifecycle</h4>
<pre>
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Proposed   │────▶│  Validated  │────▶│  Approved   │────▶│  Published  │
│   Entry     │     │ (Checks)    │     │ by Director │     │ to Students │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
        │                   │                   │                   │
        ▼                   ▼                   ▼                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Conflict   │     │  Modified   │     │  Rejected   │     │  Archived   │
│  Detected   │     │ by Admin    │     │ by Director │     │ (End Sem)   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
</pre>

<h3>Data Flow Diagrams</h3>

<h4>Course Allocation Data Flow</h4>
<pre>
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   COD       │────▶│  Course     │────▶│    DVC      │────▶│  Timetable  │
│   Creates   │     │ Allocation  │     │  Approves   │     │   Director  │
│   Courses   │     │   Database  │     │/Rejects     │     │  Schedules  │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
        │                   │                   │                   │
        ▼                   ▼                   ▼                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│Department   │     │Validation   │     │Notification │     │Timetable    │
│Database     │     │Rules        │     │System       │     │Database     │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
</pre>

<h4>Timetable Generation Data Flow</h4>
<pre>
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Approved    │────▶│  Auto-      │────▶│  Manual     │────▶│  Final      │
│ Courses     │     │ Scheduler   │     │  Adjust-    │     │ Timetable   │
│ Database    │     │  Algorithm  │     │  ments      │     │  Published  │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
        │                   │                   │                   │
        ▼                   ▼                   ▼                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│Constraint   │     │Temporary    │     │Conflict     │     │Student/     │
│Rules        │     │Timetable    │     │Resolution   │     │Lecturer     │
│Database     │     │Database     │     │Tools        │     │Portal       │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
</pre>

<h3>Entity State Diagrams</h3>

<h4>Course Allocation State Machine</h4>
<pre>
          ┌─────────────────────────────────────────────────┐
          │           Course Allocation States               │
          ├─────────────────────────────────────────────────┤
          │  • DRAFT: Initial creation by COD                │
          │  • SUBMITTED: Sent to DVC for approval          │
          │  • APPROVED: Approved by DVC                    │
          │  • REJECTED: Rejected by DVC                    │
          │  • MODIFIED: Modified after rejection           │
          │  • FORWARDED: Sent to timetable department      │
          │  • SCHEDULED: Included in timetable             │
          │  • ARCHIVED: Moved to archive after semester    │
          └─────────────────────────────────────────────────┘

          ┌─────┐     Submit    ┌──────────┐    Approve   ┌─────────┐
          │DRAFT│──────────────▶│SUBMITTED │─────────────▶│APPROVED │
          └─────┘               └──────────┘              └─────────┘
             │                       │                         │
             │ Reject                │ Modify                  │ Forward
             ▼                       ▼                         ▼
          ┌─────────┐           ┌─────────┐              ┌───────────┐
          │REJECTED │◀──────────│MODIFIED │              │FORWARDED  │
          └─────────┘           └─────────┘              └───────────┘
             │                       │                         │
             └───────────────────────┼─────────────────────────┘
                                     │
                                     ▼
                               ┌───────────┐
                               │ SCHEDULED │
                               └───────────┘
                                     │
                                     ▼
                               ┌───────────┐
                               │ ARCHIVED  │
                               └───────────┘
</pre>

<h3>Database Normalization</h3>

<h4>Normalization Levels</h4>
<ol>
<li><strong>1NF (First Normal Form)</strong>: All tables have primary keys, no repeating groups</li>
<li><strong>2NF (Second Normal Form)</strong>: All non-key attributes fully dependent on primary key</li>
<li><strong>3NF (Third Normal Form)</strong>: No transitive dependencies</li>
<li><strong>BCNF (Boyce-Codd Normal Form)</strong>: Every determinant is a candidate key</li>
</ol>

<h4>Denormalization for Performance</h4>
<p>Strategic denormalization applied in:</p>
<ul>
<li><strong>Timetable entries</strong>: Store venue as string for faster queries</li>
<li><strong>Course names</strong>: Duplicated in CourseAllocation for display efficiency</li>
<li><strong>Student counts</strong>: Aggregated in merged course groups</li>
</ul>

<h3>Entity Relationship SQL</h3>
<h4>Relationship Creation SQL</h4>
<pre><code class="sql">-- Faculty-Department Relationship
ALTER TABLE TT_APP_department
ADD CONSTRAINT fk_department_faculty
FOREIGN KEY (faculty_id)
REFERENCES TT_APP_faculty(id)
ON DELETE CASCADE;

-- Department-Program Relationship
ALTER TABLE TT_APP_program
ADD CONSTRAINT fk_program_department
FOREIGN KEY (department_id)
REFERENCES TT_APP_department(id)
ON DELETE CASCADE;

-- CourseAllocation Foreign Keys
ALTER TABLE TT_APP_courseallocation
ADD CONSTRAINT fk_courseallocation_department
FOREIGN KEY (department_id) REFERENCES TT_APP_department(id),
ADD CONSTRAINT fk_courseallocation_program
FOREIGN KEY (program_id) REFERENCES TT_APP_program(id),
ADD CONSTRAINT fk_courseallocation_lecturer
FOREIGN KEY (lecturer_id) REFERENCES TT_APP_lecturer(id)
ON DELETE SET NULL;</code></pre>

<h3>ERD Visualization Tools</h3>
<h4>Recommended Tools</h4>
<ol>
<li><strong>dbdiagram.io</strong>: Online database diagram tool</li>
<li><strong>Lucidchart</strong>: Professional diagramming</li>
<li><strong>Draw.io</strong>: Free open-source diagramming</li>
<li><strong>PlantUML</strong>: Text-based diagram generation</li>
<li><strong>pgAdmin</strong>: PostgreSQL's built-in diagram tool</li>
</ol>

<h4>PlantUML ERD Example</h4>
<pre><code class="plantuml">@startuml
!define TABLE(name,desc) class name as "desc" << (T,#FFAAAA) >>
!define ENTITY(name,desc) class name as "desc" << (E,#AAFFAA) >>

ENTITY(Faculty, "Faculty") {
  +id: INTEGER <<PK>>
  +name: VARCHAR(150) <<UNIQUE>>
  +description: TEXT
  +leader_id: INTEGER <<FK>>
}

ENTITY(Department, "Department") {
  +id: INTEGER <<PK>>
  +name: VARCHAR(150) <<UNIQUE>>
  +faculty_id: INTEGER <<FK>>
  +description: TEXT
  +leader_id: INTEGER <<FK>>
}

ENTITY(Program, "Program") {
  +id: INTEGER <<PK>>
  +name: VARCHAR(200) <<UNIQUE>>
  +department_id: INTEGER <<FK>>
  +description: TEXT
}

Faculty "1" -- "*" Department : has
Department "1" -- "*" Program : offers
Program "1" -- "*" CourseAllocation : contains

@enduml</code></pre>
"""

page3 = DocumentationPage.objects.create(
    title='Entity Relationship Diagrams - Complete ERD Documentation',
    slug='entity-relationship-diagrams-complete-erd-documentation',
    short_description='Complete entity relationship diagrams with detailed relationships and cardinalities',
    content=page3_content,
    category=categories['entity-relationship-diagrams'],
    page_type='reference',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=25,
    version='1.0'
)
print(f"   Created: {page3.title}")

# ============================================================
# PAGE 4: File Management System
# ============================================================

page4_content = """
<h2>File Management System</h2>
<p>Comprehensive file management and media handling system for the University Timetabling System.</p>

<h3>File System Architecture</h3>
<div class="file-architecture">
<pre>
┌─────────────────────────────────────────────────────────────────────┐
│                    FILE MANAGEMENT SYSTEM                            │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                     APPLICATION LAYER                       │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │   │
│  │  │   Django    │  │  File       │  │  Image      │        │   │
│  │  │   Models    │  │  Upload     │  │ Processing  │        │   │
│  │  │             │  │  Handlers   │  │             │        │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘        │   │
│  │         │                │                │               │   │
│  │  ┌──────┴────────────────┴────────────────┴──────┐        │   │
│  │  │             Storage Backends                   │        │   │
│  │  └──────────────────────┬─────────────────────────┘        │   │
│  │                         │                                  │   │
│  └─────────────────────────┼──────────────────────────────────┘   │
│                            │                                      │
│  ┌─────────────────────────┼──────────────────────────────────┐   │
│  │                     STORAGE LAYER                          │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │   │
│  │  │  Local      │  │   AWS S3    │  │  MinIO      │        │   │
│  │  │  File       │  │   (Cloud)   │  │ (Self-hosted│        │   │
│  │  │  System     │  │             │  │  S3-compat) │        │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘        │   │
│  │         │                │                │               │   │
│  │  ┌──────┴────────────────┴────────────────┴──────┐        │   │
│  │  │             File Organization                  │        │   │
│  │  │  • Media/Year/Month/Type                      │        │   │
│  │  │  • Static/Versioned Assets                    │        │   │
│  │  │  • Backup/Archives                            │        │   │
│  │  └───────────────────────────────────────────────┘        │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
</pre>
</div>

<h3>File Storage Configuration</h3>
<h4>Django Storage Settings</h4>
<pre><code class="python"># settings.py - File Storage Configuration

# Default file storage
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'

# Local development storage (fallback)
if DEBUG:
    DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
    MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
    MEDIA_URL = '/media/'

# Production S3 storage
else:
    AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME')
    AWS_S3_REGION_NAME = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
    AWS_S3_CUSTOM_DOMAIN = f'{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com'
    AWS_S3_OBJECT_PARAMETERS = {
        'CacheControl': 'max-age=86400',
    }
    AWS_LOCATION = 'media'
    AWS_DEFAULT_ACL = 'private'
    AWS_QUERYSTRING_AUTH = True
    AWS_QUERYSTRING_EXPIRE = 3600  # 1 hour
    
    MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/{AWS_LOCATION}/'
    MEDIA_ROOT = ''

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]

# Static files storage
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'</code></pre>

<h3>File Organization Structure</h3>
<h4>Directory Tree</h4>
<pre>
university_timetabling/
├── media/                          # Uploaded media files
│   ├── documentation/              # Documentation images
│   │   ├── images/
│   │   │   ├── 2024/
│   │   │   │   ├── 01/            # January 2024
│   │   │   │   │   ├── diagrams/
│   │   │   │   │   ├── screenshots/
│   │   │   │   │   └── tutorials/
│   │   │   │   └── 02/            # February 2024
│   │   │   └── attachments/
│   │   │       ├── pdfs/
│   │   │       ├── docs/
│   │   │       └── spreadsheets/
│   ├── foods/                      # Food service images
│   │   ├── menu_items/
│   │   │   ├── breakfast/
│   │   │   ├── lunch/
│   │   │   └── dinner/
│   │   └── offers/
│   ├── profiles/                   # User profile pictures
│   │   ├── lecturers/
│   │   ├── students/
│   │   └── staff/
│   └── imports/                    # Data import files
│       ├── courses/
│       ├── students/
│       └── timetables/
├── static/                         # Static assets
│   ├── css/
│   │   ├── app.css
│   │   ├── bootstrap.css
│   │   └── custom.css
│   ├── js/
│   │   ├── app.js
│   │   ├── timetable.js
│   │   └── admin.js
│   ├── img/
│   │   ├── logo.png
│   │   ├── icons/
│   │   └── backgrounds/
│   └── fonts/
│       ├── roboto/
│       └── font-awesome/
├── staticfiles/                    # Collected static files
└── backups/                        # System backups
    ├── database/
    │   ├── daily/
    │   ├── weekly/
    │   └── monthly/
    └── media/
        └── archives/
</pre>

<h3>Media File Models</h3>
<h4>Documentation Image Model</h4>
<pre><code class="python">class DocumentationImage(models.Model):
    \"\"\"Images for documentation content\"\"\"
    page = models.ForeignKey(DocumentationPage, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(
        upload_to='documentation/images/%Y/%m/',
        verbose_name='Documentation Image'
    )
    caption = models.CharField(max_length=200, blank=True)
    alt_text = models.CharField(max_length=200)
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # File validation
    def clean(self):
        from django.core.exceptions import ValidationError
        import os
        
        # Check file extension
        allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp']
        ext = os.path.splitext(self.image.name)[1].lower()
        if ext not in allowed_extensions:
            raise ValidationError(f'File type {ext} not allowed. Allowed: {allowed_extensions}')
        
        # Check file size (5MB limit)
        max_size = 5 * 1024 * 1024  # 5MB
        if self.image.size > max_size:
            raise ValidationError(f'File size exceeds {max_size/1024/1024}MB limit')
    
    class Meta:
        ordering = ['order']
        verbose_name = 'Documentation Image'
        verbose_name_plural = 'Documentation Images'
    
    def __str__(self):
        return self.caption or f"Image for {self.page.title}"
    
    def get_absolute_url(self):
        return self.image.url
    
    def get_file_size(self):
        \"\"\"Return human-readable file size\"\"\"
        size = self.image.size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} TB"</code></pre>

<h4>Food Image Model</h4>
<pre><code class="python">class FoodImage(models.Model):
    \"\"\"Images for food items in cafeteria\"\"\"
    food = models.ForeignKey(Food, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField(
        upload_to='foods/%Y/%m/',
        verbose_name='Food Image'
    )
    is_primary = models.BooleanField(default=False, help_text='Primary display image')
    created_at = models.DateTimeField(auto_now_add=True)
    
    def save(self, *args, **kwargs):
        # If this is set as primary, unset others
        if self.is_primary:
            FoodImage.objects.filter(food=self.food, is_primary=True).update(is_primary=False)
        super().save(*args, **kwargs)
    
    class Meta:
        ordering = ['-is_primary', 'created_at']
        verbose_name = 'Food Image'
        verbose_name_plural = 'Food Images'
    
    def __str__(self):
        return f"Image for {self.food.name}"</code></pre>

<h3>File Upload Handlers</h3>
<h4>Custom File Upload Handler</h4>
<pre><code class="python"># file_handlers.py
import os
from django.core.files.uploadhandler import TemporaryFileUploadHandler
from django.core.exceptions import ValidationError
from PIL import Image
import magic

class ValidatedImageUploadHandler(TemporaryFileUploadHandler):
    \"\"\"
    Custom upload handler that validates images before saving.
    \"\"\"
    
    ALLOWED_MIME_TYPES = [
        'image/jpeg',
        'image/png',
        'image/gif',
        'image/webp',
        'image/svg+xml',
    ]
    
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    MAX_IMAGE_DIMENSIONS = (4000, 4000)  # Max width, height
    
    def receive_data_chunk(self, raw_data, start):
        # Check file size during upload
        if self.file_size > self.MAX_FILE_SIZE:
            raise ValidationError(f'File size exceeds {self.MAX_FILE_SIZE/1024/1024}MB limit')
        
        super().receive_data_chunk(raw_data, start)
    
    def file_complete(self, file_size):
        file = super().file_complete(file_size)
        
        if file:
            # Validate MIME type
            mime = magic.Magic(mime=True)
            file_mime = mime.from_buffer(file.read(2048))
            file.seek(0)
            
            if file_mime not in self.ALLOWED_MIME_TYPES:
                raise ValidationError(f'Invalid file type: {file_mime}')
            
            # For images, validate dimensions
            if file_mime.startswith('image/'):
                try:
                    with Image.open(file) as img:
                        width, height = img.size
                        if width > self.MAX_IMAGE_DIMENSIONS[0] or height > self.MAX_IMAGE_DIMENSIONS[1]:
                            raise ValidationError(
                                f'Image dimensions {width}x{height} exceed maximum {self.MAX_IMAGE_DIMENSIONS[0]}x{self.MAX_IMAGE_DIMENSIONS[1]}'
                            )
                except Exception as e:
                    raise ValidationError(f'Invalid image file: {str(e)}')
            
            file.seek(0)
        
        return file</code></pre>

<h4>File Upload View</h4>
<pre><code class="python"># views.py - File upload view with validation
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.core.files.uploadhandler import FileUploadHandler
import json

@csrf_exempt
def upload_documentation_image(request):
    \"\"\"
    Handle documentation image uploads with validation.
    \"\"\"
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    if not request.user.has_perm('documentation.can_upload_images'):
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    try:
        # Use custom upload handler
        request.upload_handlers = [ValidatedImageUploadHandler(request)]
        
        page_id = request.POST.get('page_id')
        caption = request.POST.get('caption', '')
        alt_text = request.POST.get('alt_text', '')
        
        if not page_id:
            return JsonResponse({'error': 'Page ID required'}, status=400)
        
        page = DocumentationPage.objects.get(id=page_id)
        
        if 'image' not in request.FILES:
            return JsonResponse({'error': 'No image file provided'}, status=400)
        
        image_file = request.FILES['image']
        
        # Create DocumentationImage instance
        doc_image = DocumentationImage(
            page=page,
            caption=caption,
            alt_text=alt_text,
            image=image_file
        )
        
        # Full validation
        doc_image.full_clean()
        doc_image.save()
        
        return JsonResponse({
            'success': True,
            'image': {
                'id': doc_image.id,
                'url': doc_image.image.url,
                'caption': doc_image.caption,
                'alt_text': doc_image.alt_text,
                'file_size': doc_image.get_file_size(),
            }
        })
        
    except DocumentationPage.DoesNotExist:
        return JsonResponse({'error': 'Documentation page not found'}, status=404)
    except ValidationError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'error': f'Upload failed: {str(e)}'}, status=500)</code></pre>

<h3>File Processing and Optimization</h3>
<h4>Image Processing Service</h4>
<pre><code class="python"># services/image_processor.py
from PIL import Image, ImageOps
import io
from django.core.files.base import ContentFile
import os

class ImageProcessor:
    \"\"\"
    Service for processing and optimizing images.
    \"\"\"
    
    @staticmethod
    def create_thumbnails(image_field, sizes=None):
        \"\"\"
        Create thumbnails for an image field.
        
        Args:
            image_field: Django ImageField instance
            sizes: List of tuples [(width, height), ...]
        
        Returns:
            dict: Thumbnail URLs
        \"\"\"
        if sizes is None:
            sizes = [
                (100, 100),   # Small thumbnail
                (300, 300),   # Medium thumbnail
                (600, 600),   # Large thumbnail
            ]
        
        thumbnails = {}
        original_path = image_field.path
        
        for size in sizes:
            width, height = size
            
            # Generate thumbnail filename
            filename = os.path.basename(original_path)
            name, ext = os.path.splitext(filename)
            thumb_name = f"{name}_{width}x{height}{ext}"
            thumb_path = os.path.join(os.path.dirname(original_path), 'thumbnails', thumb_name)
            
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
            
            # Create thumbnail
            with Image.open(original_path) as img:
                # Convert to RGB if necessary
                if img.mode in ('RGBA', 'LA'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else img)
                    img = background
                
                # Create thumbnail
                img.thumbnail((width, height), Image.Resampling.LANCZOS)
                img.save(thumb_path, 'JPEG', quality=85, optimize=True)
            
            thumbnails[f"{width}x{height}"] = thumb_path
        
        return thumbnails
    
    @staticmethod
    def optimize_image(image_field, quality=85, max_width=2000):
        \"\"\"
        Optimize an image for web delivery.
        
        Args:
            image_field: Django ImageField instance
            quality: JPEG quality (1-100)
            max_width: Maximum width for resizing
        
        Returns:
            bool: Success status
        \"\"\"
        try:
            with Image.open(image_field.path) as img:
                # Calculate new dimensions
                width, height = img.size
                if width > max_width:
                    ratio = max_width / float(width)
                    new_height = int(float(height) * ratio)
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                
                # Convert to RGB if necessary
                if img.mode in ('RGBA', 'LA'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else img)
                    img = background
                
                # Save optimized image
                img.save(image_field.path, 'JPEG', quality=quality, optimize=True)
                
                # Update file size in model if applicable
                if hasattr(image_field.instance, 'file_size'):
                    image_field.instance.file_size = os.path.getsize(image_field.path)
                    image_field.instance.save(update_fields=['file_size'])
                
                return True
                
        except Exception as e:
            print(f"Image optimization failed: {e}")
            return False
    
    @staticmethod
    def extract_exif_data(image_field):
        \"\"\"
        Extract EXIF data from an image.
        
        Args:
            image_field: Django ImageField instance
        
        Returns:
            dict: EXIF data
        \"\"\"
        try:
            with Image.open(image_field.path) as img:
                exif_data = img._getexif()
                if exif_data:
                    # Convert to readable format
                    readable_exif = {}
                    for tag, value in exif_data.items():
                        tag_name = Image.ExifTags.TAGS.get(tag, tag)
                        readable_exif[tag_name] = str(value)
                    return readable_exif
        except Exception:
            pass
        
        return {}</code></pre>

<h3>File Security</h3>
<h4>Security Measures</h4>
<ol>
<li><strong>File Type Validation</strong>: Whitelist allowed file types</li>
<li><strong>Virus Scanning</strong>: Scan uploaded files for malware</li>
<li><strong>Size Limits</strong>: Enforce maximum file sizes</li>
<li><strong>Access Control</strong>: Implement proper permissions</li>
<li><strong>Secure URLs</strong>: Use signed URLs for sensitive files</li>
</ol>

<h4>Secure File Download View</h4>
<pre><code class="python"># views.py - Secure file download
from django.http import FileResponse, Http404
from django.contrib.auth.decorators import login_required
from django.conf import settings
import os

@login_required
def secure_download(request, file_path):
    \"\"\"
    Serve files securely with access control.
    \"\"\"
    # Validate file path
    safe_path = os.path.normpath(file_path)
    if '..' in safe_path or safe_path.startswith('/'):
        raise Http404("Invalid file path")
    
    # Construct full path
    full_path = os.path.join(settings.MEDIA_ROOT, safe_path)
    
    # Check if file exists
    if not os.path.exists(full_path):
        raise Http404("File not found")
    
    # Check permissions based on file type
    if safe_path.startswith('documentation/'):
        if not request.user.has_perm('documentation.view_documentation'):
            raise Http404("Permission denied")
    
    elif safe_path.startswith('profiles/'):
        # Only allow users to view their own profile pictures
        # or admins to view all
        if not (request.user.is_staff or safe_path.endswith(f'{request.user.username}.jpg')):
            raise Http404("Permission denied")
    
    # Serve file
    response = FileResponse(open(full_path, 'rb'))
    filename = os.path.basename(full_path)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    # Set appropriate content type
    if filename.endswith('.pdf'):
        response['Content-Type'] = 'application/pdf'
    elif filename.endswith('.jpg') or filename.endswith('.jpeg'):
        response['Content-Type'] = 'image/jpeg'
    elif filename.endswith('.png'):
        response['Content-Type'] = 'image/png'
    
    return response</code></pre>

<h3>File Backup and Archival</h3>
<h4>Backup Strategy</h4>
<pre><code class="python"># management/commands/backup_media.py
from django.core.management.base import BaseCommand
from django.conf import settings
import os
import shutil
import boto3
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Backup media files to backup directory or S3'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--destination',
            type=str,
            default='local',
            choices=['local', 's3'],
            help='Backup destination'
        )
        parser.add_argument(
            '--keep-days',
            type=int,
            default=30,
            help='Number of days to keep backups'
        )
    
    def handle(self, *args, **options):
        destination = options['destination']
        keep_days = options['keep_days']
        
        # Create backup directory
        backup_dir = os.path.join(settings.BASE_DIR, 'backups', 'media')
        os.makedirs(backup_dir, exist_ok=True)
        
        # Create timestamp for backup
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f'media_backup_{timestamp}'
        
        if destination == 'local':
            self.backup_to_local(backup_dir, backup_name)
        elif destination == 's3':
            self.backup_to_s3(backup_dir, backup_name)
        
        # Clean old backups
        self.clean_old_backups(backup_dir, keep_days)
        
        self.stdout.write(self.style.SUCCESS(f'Media backup completed: {backup_name}'))
    
    def backup_to_local(self, backup_dir, backup_name):
        \"\"\"Backup media files to local directory.\"\"\"
        media_dir = settings.MEDIA_ROOT
        backup_path = os.path.join(backup_dir, backup_name)
        
        # Create zip archive
        shutil.make_archive(backup_path, 'zip', media_dir)
        
        logger.info(f'Local backup created: {backup_path}.zip')
    
    def backup_to_s3(self, backup_dir, backup_name):
        \"\"\"Backup media files to S3.\"\"\"
        media_dir = settings.MEDIA_ROOT
        backup_path = os.path.join(backup_dir, backup_name)
        
        # Create local backup first
        shutil.make_archive(backup_path, 'zip', media_dir)
        
        # Upload to S3
        s3_client = boto3.client('s3')
        s3_key = f'media_backups/{backup_name}.zip'
        
        with open(f'{backup_path}.zip', 'rb') as f:
            s3_client.upload_fileobj(
                f,
                settings.AWS_STORAGE_BUCKET_NAME,
                s3_key,
                ExtraArgs={
                    'StorageClass': 'STANDARD_IA',
                    'Metadata': {
                        'backup_type': 'media',
                        'backup_date': datetime.now().isoformat()
                    }
                }
            )
        
        logger.info(f'S3 backup uploaded: {s3_key}')
        
        # Remove local backup file
        os.remove(f'{backup_path}.zip')
    
    def clean_old_backups(self, backup_dir, keep_days):
        \"\"\"Remove backup files older than keep_days.\"\"\"
        import time
        
        current_time = time.time()
        cutoff_time = current_time - (keep_days * 24 * 60 * 60)
        
        for filename in os.listdir(backup_dir):
            filepath = os.path.join(backup_dir, filename)
            if os.path.isfile(filepath):
                file_time = os.path.getmtime(filepath)
                if file_time < cutoff_time:
                    os.remove(filepath)
                    logger.info(f'Removed old backup: {filename}')</code></pre>

<h3>File Management API</h3>
<h4>REST API Endpoints</h4>
<pre><code class="python"># api/views.py - File management API
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.files.storage import default_storage
import os

class FileManagementViewSet(viewsets.ViewSet):
    \"\"\"
    API for file management operations.
    \"\"\"
    permission_classes = [permissions.IsAuthenticated]
    
    @action(detail=False, methods=['post'])
    def upload(self, request):
        \"\"\"
        Upload a file.
        
        Request:
            POST /api/files/upload/
            Content-Type: multipart/form-data
            
            file: The file to upload
            directory: Target directory (optional)
            metadata: JSON metadata (optional)
        \"\"\"
        if 'file' not in request.FILES:
            return Response(
                {'error': 'No file provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        file_obj = request.FILES['file']
        directory = request.data.get('directory', 'uploads')
        metadata = request.data.get('metadata', {})
        
        # Validate file
        max_size = 10 * 1024 * 1024  # 10MB
        if file_obj.size > max_size:
            return Response(
                {'error': f'File size exceeds {max_size/1024/1024}MB limit'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Generate safe filename
        import uuid
        original_name = file_obj.name
        name, ext = os.path.splitext(original_name)
        safe_name = f"{uuid.uuid4()}{ext}"
        
        # Save file
        file_path = os.path.join(directory, safe_name)
        saved_path = default_storage.save(file_path, file_obj)
        
        # Log file upload
        FileUploadLog.objects.create(
            user=request.user,
            file_path=saved_path,
            original_name=original_name,
            file_size=file_obj.size,
            metadata=metadata
        )
        
        return Response({
            'success': True,
            'file': {
                'path': saved_path,
                'url': default_storage.url(saved_path),
                'original_name': original_name,
                'size': file_obj.size,
                'metadata': metadata
            }
        })
    
    @action(detail=False, methods=['get'])
    def list(self, request):
        \"\"\"
        List files in a directory.
        
        Request:
            GET /api/files/list/?directory=uploads
        \"\"\"
        directory = request.GET.get('directory', '')
        
        try:
            # List files in directory
            files = []
            directories = []
            
            for item in default_storage.listdir(directory)[1]:  # Files
                file_path = os.path.join(directory, item) if directory else item
                files.append({
                    'name': item,
                    'path': file_path,
                    'url': default_storage.url(file_path),
                    'size': default_storage.size(file_path),
                    'modified': default_storage.get_modified_time(file_path)
                })
            
            for item in default_storage.listdir(directory)[0]:  # Directories
                dir_path = os.path.join(directory, item) if directory else item
                directories.append({
                    'name': item,
                    'path': dir_path
                })
            
            return Response({
                'directory': directory,
                'files': files,
                'directories': directories
            })
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['delete'])
    def delete(self, request):
        \"\"\"
        Delete a file.
        
        Request:
            DELETE /api/files/delete/
            {
                "path": "uploads/filename.jpg"
            }
        \"\"\"
        file_path = request.data.get('path')
        
        if not file_path:
            return Response(
                {'error': 'File path required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not default_storage.exists(file_path):
            return Response(
                {'error': 'File not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check permissions
        if not request.user.is_staff:
            return Response(
                {'error': 'Permission denied'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Delete file
        default_storage.delete(file_path)
        
        # Log deletion
        FileDeleteLog.objects.create(
            user=request.user,
            file_path=file_path
        )
        
        return Response({'success': True})</code></pre>
"""

page4 = DocumentationPage.objects.create(
    title='File Management System - Complete Documentation',
    slug='file-management-system-complete-documentation',
    short_description='Comprehensive file management system including storage, processing, and security',
    content=page4_content,
    category=categories['file-management-system'],
    page_type='reference',
    difficulty='advanced',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='admin',
    estimated_read_time=30,
    version='1.0'
)
print(f"   Created: {page4.title}")

# ============================================================
# PAGE 5: Process Flows Documentation
# ============================================================

page5_content = """
<h2>Process Flows and Workflows</h2>
<p>Detailed business process flows and workflows for the University Timetabling System.</p>

<h3>Key Business Processes</h3>

<h4>1. Course Allocation Workflow</h4>
<pre>
┌─────────────────────────────────────────────────────────────────────┐
│                   COURSE ALLOCATION WORKFLOW                        │
│                                                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐        │
│  │   COD    │──▶│  Create  │──▶│  Review  │──▶│  Submit  │        │
│  │  Starts  │   │  Course  │   │   and    │   │   to     │        │
│  │          │   │ Allocation│   │ Validate │   │   DVC    │        │
│  └──────────┘   └──────────┘   └──────────┘   └────┬─────┘        │
│                                                     │              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────┴─────┐        │
│  │   DVC    │◀──│  Notify  │◀──│  DVC     │◀──│  DVC     │        │
│  │ Reviews  │   │   COD    │   │ Decision │   │ Receives │        │
│  │          │   │          │   │          │   │ Request  │        │
│  └────┬─────┘   └──────────┘   └────┬─────┘   └──────────┘        │
│       │                              │                             │
│  ┌────┴─────┐                ┌───────┴──────┐                     │
│  │ Approve  │                │   Reject     │                     │
│  │  Course  │                │ with Reason  │                     │
│  └────┬─────┘                └───────┬──────┘                     │
│       │                              │                             │
│  ┌────┴──────────────────────────────┴─────┐                     │
│  │          Forward to Timetable           │                     │
│  └───────────────────┬─────────────────────┘                     │
│                      │                                           │
│              ┌───────┴───────┐                                   │
│              │  Timetable    │                                   │
│              │  Department   │                                   │
│              │   Receives    │                                   │
│              └───────┬───────┘                                   │
│                      │                                           │
│              ┌───────┴───────┐                                   │
│              │  Schedule     │                                   │
│              │   Course      │                                   │
│              │  in Timetable │                                   │
│              └───────────────┘                                   │
└───────────────────────────────────────────────────────────────────┘
</pre>

<h4>2. Timetable Generation Process</h4>
<pre>
┌─────────────────────────────────────────────────────────────────────┐
│                  TIMETABLE GENERATION PROCESS                       │
│                                                                     │
│  ┌────────────┐   ┌────────────┐   ┌────────────┐                 │
│  │  Collect   │──▶│  Validate  │──▶│   Run      │                 │
│  │ Approved   │   │ Constraints │   │  Auto-     │                 │
│  │  Courses   │   │            │   │ Scheduler  │                 │
│  └────────────┘   └────────────┘   └─────┬──────┘                 │
│                                           │                        │
│  ┌────────────┐   ┌────────────┐   ┌─────┴──────┐                 │
│  │  Detect    │◀──│  Generate  │◀──│  Manual    │                 │
│  │ Conflicts  │   │ Draft      │   │ Adjust-    │                 │
│  │            │   │ Timetable  │   │  ments     │                 │
│  └─────┬──────┘   └────────────┘   └────────────┘                 │
│        │                                                        │
│  ┌─────┴──────┐   ┌────────────┐   ┌────────────┐                 │
│  │  Resolve   │──▶│   Final    │──▶│  Publish   │                 │
│  │ Conflicts  │   │  Review    │   │ Timetable  │                 │
│  │            │   │            │   │            │                 │
│  └────────────┘   └────────────┘   └─────┬──────┘                 │
│                                           │                        │
│                                   ┌───────┴───────┐               │
│                                   │  Notify       │               │
│                                   │  Stakeholders │               │
│                                   └───────────────┘               │
└───────────────────────────────────────────────────────────────────┘
</pre>

<h3>Detailed Process Documentation</h3>
<h4>Course Allocation Detailed Process</h4>
<ol>
<li><strong>Initiation Phase</strong>
   <ul>
   <li>COD logs into the system</li>
   <li>Navigates to Course Allocation section</li>
   <li>Selects department and program</li>
   <li>Views existing course allocations</li>
   </ul>
</li>

<li><strong>Creation Phase</strong>
   <ul>
   <li>Click "Add New Allocation" button</li>
   <li>Fill course details (code, name, description)</li>
   <li>Select lecturer from dropdown</li>
   <li>Enter number of students</li>
   <li>Choose semester and academic year</li>
   </ul>
</li>

<li><strong>Validation Phase</strong>
   <ul>
   <li>System validates course code uniqueness</li>
   <li>Checks lecturer availability</li>
   <li>Validates student count against venue capacities</li>
   <li>Checks for scheduling conflicts</li>
   </ul>
</li>

<li><strong>Submission Phase</strong>
   <ul>
   <li>COD reviews allocation details</li>
   <li>Clicks "Submit to DVC" button</li>
   <li>System changes status to "Submitted"</li>
   <li>Notification sent to DVC</li>
   </ul>
</li>

<li><strong>Approval Phase</strong>
   <ul>
   <li>DVC receives notification</li>
   <li>Reviews course allocation details</li>
   <li>Either approves or rejects with reason</li>
   <li>System updates status accordingly</li>
   </ul>
</li>
</ol>

<h3>Process Flow Diagrams</h3>
<h4>Student Timetable Viewing Process</h4>
<pre>
Student Activity                     System Response
─────────────────────────────────────────────────────
1. Student visits portal        → Display login page
2. Enters credentials          → Authenticate user
3. Selects program/year        → Validate selection
4. Chooses timetable type      → Fetch timetable data
5. Views timetable            → Display in UI
6. Filters/search             → Update display
7. Downloads/prints           → Generate PDF/Excel
8. Logs out                   → Clear session
</pre>

<h4>Exam Scheduling Process</h4>
<pre>
Step 1: Configuration
├── Set exam period dates
├── Define time slots
├── Configure excluded dates
└── Set venue capacities

Step 2: Data Preparation
├── Collect approved courses
├── Group by program/year
├── Calculate student counts
└── Identify special requirements

Step 3: Auto-Scheduling
├── Run scheduling algorithm
├── Check all constraints
├── Handle conflicts
└── Generate draft schedule

Step 4: Manual Adjustment
├── Review auto-schedule
├── Make manual changes
├── Resolve remaining conflicts
└── Final validation

Step 5: Publication
├── Approve final schedule
├── Publish to students
├── Send notifications
└── Archive previous schedule
</pre>

<h3>Workflow State Machines</h3>
<h4>Course Allocation State Machine</h4>
<pre><code class="python"># workflow/states.py
from django_fsm import FSMField, transition

class CourseAllocationWorkflow:
    \"\"\"
    State machine for course allocation workflow.
    \"\"\"
    
    # States
    DRAFT = 'draft'
    SUBMITTED = 'submitted'
    UNDER_REVIEW = 'under_review'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    MODIFIED = 'modified'
    FORWARDED = 'forwarded'
    SCHEDULED = 'scheduled'
    ARCHIVED = 'archived'
    
    STATE_CHOICES = [
        (DRAFT, 'Draft'),
        (SUBMITTED, 'Submitted to DVC'),
        (UNDER_REVIEW, 'Under Review'),
        (APPROVED, 'Approved by DVC'),
        (REJECTED, 'Rejected by DVC'),
        (MODIFIED, 'Modified after rejection'),
        (FORWARDED, 'Forwarded to Timetable'),
        (SCHEDULED, 'Scheduled in Timetable'),
        (ARCHIVED, 'Archived'),
    ]
    
    # State field
    state = FSMField(
        default=DRAFT,
        choices=STATE_CHOICES,
        protected=True
    )
    
    @transition(field=state, source=DRAFT, target=SUBMITTED)
    def submit_to_dvc(self, submitted_by):
        \"\"\"
        Submit course allocation to DVC for approval.
        \"\"\"
        self.submitted_by = submitted_by
        self.submitted_at = timezone.now()
        
        # Send notification to DVC
        self.send_notification(
            recipient=self.get_dvc_user(),
            message=f'Course allocation submitted: {self.course_code}',
            action_url=self.get_absolute_url()
        )
    
    @transition(field=state, source=SUBMITTED, target=APPROVED)
    def approve_by_dvc(self, approved_by, notes=None):
        \"\"\"
        DVC approves the course allocation.
        \"\"\"
        self.approved_by = approved_by
        self.approved_at = timezone.now()
        self.approval_notes = notes
        
        # Send notification to COD
        self.send_notification(
            recipient=self.department.leader,
            message=f'Course allocation approved: {self.course_code}',
            action_url=self.get_absolute_url()
        )
    
    @transition(field=state, source=SUBMITTED, target=REJECTED)
    def reject_by_dvc(self, rejected_by, reason):
        \"\"\"
        DVC rejects the course allocation.
        \"\"\"
        self.rejected_by = rejected_by
        self.rejected_at = timezone.now()
        self.rejection_reason = reason
        
        # Send notification to COD
        self.send_notification(
            recipient=self.department.leader,
            message=f'Course allocation rejected: {self.course_code}',
            details=reason,
            action_url=self.get_absolute_url()
        )
    
    @transition(field=state, source=REJECTED, target=MODIFIED)
    def modify_after_rejection(self, modified_by):
        \"\"\"
        COD modifies the allocation after rejection.
        \"\"\"
        self.modified_by = modified_by
        self.modified_at = timezone.now()
    
    @transition(field=state, source=[APPROVED, MODIFIED], target=FORWARDED)
    def forward_to_timetable(self, forwarded_by):
        \"\"\"
        Forward approved allocation to timetable department.
        \"\"\"
        self.forwarded_by = forwarded_by
        self.forwarded_at = timezone.now()
        self.submitted_to_tt = True
        
        # Send notification to timetable department
        self.send_notification(
            recipient=self.get_timetable_director(),
            message=f'Course allocation forwarded: {self.course_code}',
            action_url=self.get_absolute_url()
        )</code></pre>

<h3>Business Rules and Constraints</h3>
<h4>Scheduling Constraints</h4>
<pre><code class="python"># constraints/scheduling.py
from django.core.exceptions import ValidationError
from datetime import time

class SchedulingConstraints:
    \"\"\"
    Business rules for scheduling constraints.
    \"\"\"
    
    @staticmethod
    def validate_venue_availability(venue, day, start_time, end_time, exclude_id=None):
        \"\"\"
        Check if venue is available at given time.
        \"\"\"
        conflicts = Timetable.objects.filter(
            venue=venue,
            day=day,
        ).exclude(id=exclude_id).filter(
            start_time__lt=end_time,
            end_time__gt=start_time,
        )
        
        if conflicts.exists():
            raise ValidationError(
                f'Venue {venue} already booked on {day} from '
                f'{conflicts.first().start_time} to {conflicts.first().end_time}'
            )
    
    @staticmethod
    def validate_lecturer_availability(lecturer, day, start_time, end_time, exclude_id=None):
        \"\"\"
        Check if lecturer is available at given time.
        \"\"\"
        conflicts = Timetable.objects.filter(
            course_allocation__lecturer=lecturer,
            day=day,
        ).exclude(id=exclude_id).filter(
            start_time__lt=end_time,
            end_time__gt=start_time,
        )
        
        if conflicts.exists():
            raise ValidationError(
                f'Lecturer {lecturer.display_name} already has class on {day}'
            )
    
    @staticmethod
    def validate_program_schedule(program, day, start_time, end_time, exclude_id=None):
        \"\"\"
        Check if program already has class at given time.
        \"\"\"
        conflicts = Timetable.objects.filter(
            course_allocation__program=program,
            day=day,
        ).exclude(id=exclude_id).filter(
            start_time__lt=end_time,
            end_time__gt=start_time,
        )
        
        if conflicts.exists():
            raise ValidationError(
                f'Program {program.name} already has class scheduled on {day}'
            )
    
    @staticmethod
    def validate_time_slot(start_time, end_time):
        \"\"\"
        Validate time slot is within allowed hours.
        \"\"\"
        earliest_start = time(8, 0)   # 8:00 AM
        latest_end = time(20, 0)      # 8:00 PM
        
        if start_time < earliest_start:
            raise ValidationError(f'Classes cannot start before {earliest_start}')
        
        if end_time > latest_end:
            raise ValidationError(f'Classes cannot end after {latest_end}')
        
        # Minimum class duration: 1 hour
        duration = (
            (end_time.hour * 60 + end_time.minute) -
            (start_time.hour * 60 + start_time.minute)
        )
        if duration < 60:
            raise ValidationError('Minimum class duration is 1 hour')
        
        # Maximum class duration: 3 hours
        if duration > 180:
            raise ValidationError('Maximum class duration is 3 hours')</code></pre>

<h3>Process Metrics and Monitoring</h3>
<h4>Key Performance Indicators</h4>
<table class="table table-bordered">
<thead><tr><th>KPI</th><th>Description</th><th>Target</th><th>Measurement</th></tr></thead>
<tbody>
<tr><td>Course Allocation Time</td><td>Time from creation to approval</td><td>≤ 3 days</td><td>Average processing time</td></tr>
<tr><td>Timetable Generation Time</td><td>Time to generate complete timetable</td><td>≤ 2 weeks</td><td>Total elapsed time</td></tr>
<tr><td>Conflict Resolution Rate</td><td>Percentage of conflicts resolved automatically</td><td>≥ 85%</td><td>Auto-resolved / Total conflicts</td></tr>
<tr><td>User Satisfaction</td><td>User satisfaction with timetables</td><td>≥ 90%</td><td>Survey scores</td></tr>
<tr><td>System Uptime</td><td>System availability</td><td>≥ 99.5%</td><td>Uptime percentage</td></tr>
</tbody>
</table>

<h4>Process Monitoring Dashboard</h4>
<pre><code class="python"># monitoring/dashboard.py
from django.db.models import Count, Avg, Q
from datetime import datetime, timedelta

class ProcessMonitoring:
    \"\"\"
    Process monitoring and analytics.
    \"\"\"
    
    @staticmethod
    def get_course_allocation_metrics(timeframe_days=30):
        \"\"\"
        Get course allocation process metrics.
        \"\"\"
        cutoff_date = datetime.now() - timedelta(days=timeframe_days)
        
        metrics = {
            'total_allocations': CourseAllocation.objects.count(),
            'allocations_last_30_days': CourseAllocation.objects.filter(
                created_at__gte=cutoff_date
            ).count(),
            
            'status_distribution': CourseAllocation.objects.values('state').annotate(
                count=Count('id')
            ).order_by('-count'),
            
            'average_processing_time': CourseAllocation.objects.filter(
                state__in=['APPROVED', 'REJECTED']
            ).aggregate(
                avg_time=Avg(
                    models.F('approved_at') - models.F('submitted_at')
                )
            ),
            
            'approval_rate': CourseAllocation.objects.filter(
                created_at__gte=cutoff_date
            ).aggregate(
                approval_rate=models.Avg(
                    models.Case(
                        models.When(state='APPROVED', then=1),
                        default=0,
                        output_field=models.FloatField()
                    )
                )
            ),
        }
        
        return metrics
    
    @staticmethod
    def get_timetable_generation_metrics():
        \"\"\"
        Get timetable generation process metrics.
        \"\"\"
        latest_timetable = Timetable.objects.order_by('-created_at').first()
        
        if not latest_timetable:
            return {}
        
        metrics = {
            'total_entries': Timetable.objects.count(),
            'courses_scheduled': Timetable.objects.values('course_allocation').distinct().count(),
            'venues_used': Timetable.objects.values('venue').distinct().count(),
            'days_covered': Timetable.objects.values('day').distinct().count(),
            
            'conflict_statistics': {
                'total_conflicts': TimetableConflict.objects.count(),
                'resolved_conflicts': TimetableConflict.objects.filter(resolved=True).count(),
                'auto_resolved': TimetableConflict.objects.filter(
                    resolved=True,
                    resolved_automatically=True
                ).count(),
            },
            
            'generation_time': None,  # Would track from start to completion
        }
        
        return metrics</code></pre>
"""

page5 = DocumentationPage.objects.create(
    title='Process Flows and Workflows - Complete Documentation',
    slug='process-flows-workflows-complete-documentation',
    short_description='Detailed business process flows, workflows, and state machines',
    content=page5_content,
    category=categories['process-flows'],
    page_type='guide',
    difficulty='intermediate',
    order=1,
    is_published=True,
    author=author,
    requires_login=True,
    access_level='management',
    estimated_read_time=25,
    version='1.0'
)
print(f"   Created: {page5.title}")

# ============================================================
# 4. CREATE TAGS AND INTERNAL LINKS
# ============================================================

print("\n4. CREATING TAGS AND INTERNAL LINKS...")

# Create tags for system design
system_design_tags = [
    {'name': 'Architecture', 'slug': 'architecture', 'color': '#3498db'},
    {'name': 'Database Design', 'slug': 'database-design', 'color': '#2ecc71'},
    {'name': 'ERD', 'slug': 'erd', 'color': '#e74c3c'},
    {'name': 'File Management', 'slug': 'file-management', 'color': '#9b59b6'},
    {'name': 'Process Flow', 'slug': 'process-flow', 'color': '#f39c12'},
    {'name': 'System Design', 'slug': 'system-design', 'color': '#1abc9c'},
    {'name': 'Technology Stack', 'slug': 'technology-stack', 'color': '#34495e'},
    {'name': 'Deployment', 'slug': 'deployment', 'color': '#e67e22'},
    {'name': 'Security', 'slug': 'security', 'color': '#27ae60'},
    {'name': 'API', 'slug': 'api', 'color': '#8e44ad'},
]

tags = {}
for tag_data in system_design_tags:
    tag, created = DocumentationTag.objects.get_or_create(
        slug=tag_data['slug'],
        defaults=tag_data
    )
    tags[tag_data['slug']] = tag
    status = "Created" if created else "Exists"
    print(f"   {status}: {tag.name}")

# Assign tags to pages
tag_assignments = {
    page1: ['architecture', 'technology-stack', 'deployment', 'system-design'],
    page2: ['database-design', 'erd', 'system-design'],
    page3: ['erd', 'database-design', 'system-design'],
    page4: ['file-management', 'system-design', 'security'],
    page5: ['process-flow', 'system-design'],
}

for page, tag_slugs in tag_assignments.items():
    for tag_slug in tag_slugs:
        if tag_slug in tags:
            tag = tags[tag_slug]
            PageTag.objects.get_or_create(
                page=page,
                tag=tag
            )
    print(f"   Assigned {len(tag_slugs)} tags to: {page.title}")

# Create internal links between system design pages
internal_links = [
    (page1, page2, "System architecture includes database design"),
    (page1, page4, "Architecture includes file management system"),
    (page2, page3, "Database design detailed in ERD diagrams"),
    (page3, page5, "Entity relationships affect process flows"),
    (page4, page1, "File management is part of overall architecture"),
    (page5, page2, "Process flows interact with database design"),
]

for from_page, to_page, description in internal_links:
    InternalLink.objects.get_or_create(
        from_page=from_page,
        to_page=to_page,
        defaults={'description': description}
    )
    print(f"   Linked: {from_page.title} → {to_page.title}")

# ============================================================
# 5. CREATE SECTIONS WITH DETAILED CODE EXAMPLES
# ============================================================

print("\n5. CREATING DETAILED SECTIONS WITH CODE EXAMPLES...")

# Section for Database Design
section_db = DocumentationSection.objects.create(
    page=page2,
    title='Complete Database Schema SQL',
    content='''
<h3>Complete Database Schema SQL</h3>
<p>Complete SQL schema for the University Timetabling System database.</p>
''',
    order=2,
    is_active=True,
    slug='complete-database-schema-sql'
)
print(f"   Created section: {section_db.title}")

# Code Example: Complete Database Schema
code_db = CodeExample.objects.create(
    section=section_db,
    title='Complete Database Schema SQL',
    code='''-- University Timetabling System - Complete Database Schema
-- Generated: ''' + datetime.now().strftime('%Y-%m-%d') + '''

-- ============================================================
-- 1. CORE ACADEMIC STRUCTURE
-- ============================================================

-- Faculty table
CREATE TABLE TT_APP_faculty (
    id SERIAL PRIMARY KEY,
    name VARCHAR(150) UNIQUE NOT NULL,
    description TEXT,
    leader_id INTEGER REFERENCES auth_user(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_faculty_name ON TT_APP_faculty(name);
CREATE INDEX idx_faculty_leader ON TT_APP_faculty(leader_id);

-- Department table
CREATE TABLE TT_APP_department (
    id SERIAL PRIMARY KEY,
    name VARCHAR(150) UNIQUE NOT NULL,
    faculty_id INTEGER NOT NULL REFERENCES TT_APP_faculty(id) ON DELETE CASCADE,
    description TEXT,
    leader_id INTEGER REFERENCES auth_user(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_department_name ON TT_APP_department(name);
CREATE INDEX idx_department_faculty ON TT_APP_department(faculty_id);
CREATE INDEX idx_department_leader ON TT_APP_department(leader_id);

-- Program table
CREATE TABLE TT_APP_program (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) UNIQUE NOT NULL,
    department_id INTEGER NOT NULL REFERENCES TT_APP_department(id) ON DELETE CASCADE,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_program_name ON TT_APP_program(name);
CREATE INDEX idx_program_department ON TT_APP_program(department_id);

-- ============================================================
-- 2. COURSE MANAGEMENT
-- ============================================================

-- ProgramCourse table
CREATE TABLE TT_APP_programcourse (
    id SERIAL PRIMARY KEY,
    program_id INTEGER NOT NULL REFERENCES TT_APP_program(id) ON DELETE CASCADE,
    course_code VARCHAR(20) NOT NULL,
    course_name VARCHAR(200) NOT NULL,
    year SMALLINT NOT NULL CHECK (year BETWEEN 1 AND 6),
    semester SMALLINT NOT NULL CHECK (semester IN (1, 2)),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Unique constraint: course per program/year/semester
    UNIQUE(program_id, course_code, year, semester)
);

CREATE INDEX idx_programcourse_program ON TT_APP_programcourse(program_id);
CREATE INDEX idx_programcourse_code ON TT_APP_programcourse(course_code);
CREATE INDEX idx_programcourse_program_year_semester ON TT_APP_programcourse(program_id, year, semester);

-- CourseAllocation table
CREATE TABLE TT_APP_courseallocation (
    id SERIAL PRIMARY KEY,
    course_code VARCHAR(20) NOT NULL,
    course_name VARCHAR(200) NOT NULL,
    department_id INTEGER NOT NULL REFERENCES TT_APP_department(id) ON DELETE CASCADE,
    origin_department_id INTEGER REFERENCES TT_APP_department(id) ON DELETE SET NULL,
    program_id INTEGER REFERENCES TT_APP_program(id) ON DELETE CASCADE,
    lecturer_id INTEGER REFERENCES TT_APP_lecturer(id) ON DELETE SET NULL,
    number_of_students INTEGER DEFAULT 0 CHECK (number_of_students >= 0),
    
    -- Approval workflow fields
    approved_by_dvc BOOLEAN DEFAULT FALSE,
    rejected_by_dvc BOOLEAN DEFAULT FALSE,
    reason_for_disapproval TEXT DEFAULT 'No reason yet',
    submitted_to_tt BOOLEAN DEFAULT FALSE,
    
    -- State tracking
    state VARCHAR(50) DEFAULT 'draft',
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    submitted_at TIMESTAMP WITH TIME ZONE,
    approved_at TIMESTAMP WITH TIME ZONE,
    rejected_at TIMESTAMP WITH TIME ZONE,
    
    -- Check constraint: cannot be both approved and rejected
    CHECK (NOT (approved_by_dvc = TRUE AND rejected_by_dvc = TRUE))
);

CREATE INDEX idx_courseallocation_department ON TT_APP_courseallocation(department_id);
CREATE INDEX idx_courseallocation_program ON TT_APP_courseallocation(program_id);
CREATE INDEX idx_courseallocation_lecturer ON TT_APP_courseallocation(lecturer_id);
CREATE INDEX idx_courseallocation_state ON TT_APP_courseallocation(state);
CREATE INDEX idx_courseallocation_approval ON TT_APP_courseallocation(approved_by_dvc, rejected_by_dvc);

-- ============================================================
-- 3. TIMETABLE MANAGEMENT
-- ============================================================

-- Timetable table (Main class schedule)
CREATE TABLE TT_APP_timetable (
    id SERIAL PRIMARY KEY,
    course_allocation_id INTEGER NOT NULL REFERENCES TT_APP_courseallocation(id) ON DELETE CASCADE,
    venue VARCHAR(100) NOT NULL,
    day VARCHAR(20) NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Prevent double booking of venues
    UNIQUE(venue, day, start_time, end_time)
);

CREATE INDEX idx_timetable_course ON TT_APP_timetable(course_allocation_id);
CREATE INDEX idx_timetable_day_time ON TT_APP_timetable(day, start_time);
CREATE INDEX idx_timetable_venue_day ON TT_APP_timetable(venue, day);

-- ExamTimetable table
CREATE TABLE TT_APP_examtimetable (
    id SERIAL PRIMARY KEY,
    course_allocation_id INTEGER NOT NULL REFERENCES TT_APP_courseallocation(id) ON DELETE CASCADE,
    venue VARCHAR(100) NOT NULL,
    day VARCHAR(20) NOT NULL,
    date DATE NOT NULL DEFAULT CURRENT_DATE,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Prevent exam venue double booking
    UNIQUE(venue, date, start_time, end_time)
);

CREATE INDEX idx_examtimetable_course ON TT_APP_examtimetable(course_allocation_id);
CREATE INDEX idx_examtimetable_date_time ON TT_APP_examtimetable(date, start_time);
CREATE INDEX idx_examtimetable_venue_date ON TT_APP_examtimetable(venue, date);

-- ============================================================
-- 4. USER MANAGEMENT
-- ============================================================

-- Lecturer table
CREATE TABLE TT_APP_lecturer (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE REFERENCES auth_user(id) ON DELETE CASCADE,
    payroll_number VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(200) NOT NULL,
    email VARCHAR(254) UNIQUE NOT NULL,
    designation VARCHAR(10) NOT NULL CHECK (designation IN ('Prof', 'Dr', 'Mr', 'Ms', 'Mrs')),
    department_id INTEGER REFERENCES TT_APP_department(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_lecturer_payroll ON TT_APP_lecturer(payroll_number);
CREATE INDEX idx_lecturer_email ON TT_APP_lecturer(email);
CREATE INDEX idx_lecturer_department ON TT_APP_lecturer(department_id);
CREATE INDEX idx_lecturer_user ON TT_APP_lecturer(user_id);

-- ClassRep table
CREATE TABLE TT_APP_classrep (
    id SERIAL PRIMARY KEY,
    full_name VARCHAR(150),
    reg_no VARCHAR(50) UNIQUE,
    username VARCHAR(50) UNIQUE,
    email VARCHAR(254) UNIQUE,
    password VARCHAR(128) NOT NULL DEFAULT '123',
    program_id INTEGER NOT NULL REFERENCES TT_APP_program(id) ON DELETE CASCADE,
    date_registered TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    active BOOLEAN DEFAULT TRUE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_classrep_username ON TT_APP_classrep(username);
CREATE INDEX idx_classrep_reg_no ON TT_APP_classrep(reg_no);
CREATE INDEX idx_classrep_program ON TT_APP_classrep(program_id);
CREATE INDEX idx_classrep_active ON TT_APP_classrep(active);

-- ============================================================
-- 5. FILE MANAGEMENT
-- ============================================================

-- DocumentationImage table
CREATE TABLE TT_APP_documentationimage (
    id SERIAL PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES TT_APP_documentationpage(id) ON DELETE CASCADE,
    image VARCHAR(100) NOT NULL,
    caption VARCHAR(200),
    alt_text VARCHAR(200) NOT NULL,
    "order" INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_docimage_page ON TT_APP_documentationimage(page_id);
CREATE INDEX idx_docimage_order ON TT_APP_documentationimage("order");

-- ============================================================
-- 6. VIEWS FOR REPORTING
-- ============================================================

-- Department course summary view
CREATE OR REPLACE VIEW department_course_summary AS
SELECT 
    d.id as department_id,
    d.name as department_name,
    f.name as faculty_name,
    COUNT(DISTINCT ca.id) as total_courses,
    COUNT(DISTINCT CASE WHEN ca.approved_by_dvc THEN ca.id END) as approved_courses,
    COUNT(DISTINCT CASE WHEN ca.rejected_by_dvc THEN ca.id END) as rejected_courses,
    COUNT(DISTINCT CASE WHEN ca.submitted_to_tt THEN ca.id END) as submitted_courses,
    SUM(ca.number_of_students) as total_students,
    AVG(ca.number_of_students) as avg_students_per_course
FROM TT_APP_department d
JOIN TT_APP_faculty f ON d.faculty_id = f.id
LEFT JOIN TT_APP_courseallocation ca ON d.id = ca.department_id
GROUP BY d.id, f.id, d.name, f.name;

-- Timetable conflict detection view
CREATE OR REPLACE VIEW timetable_conflicts AS
SELECT 
    t1.id as timetable1_id,
    t2.id as timetable2_id,
    t1.venue,
    t1.day,
    t1.start_time,
    t1.end_time,
    ca1.course_code as course1_code,
    ca2.course_code as course2_code,
    CASE 
        WHEN ca1.id = ca2.id THEN 'Duplicate Course'
        WHEN t1.venue = t2.venue THEN 'Venue Conflict'
        WHEN ca1.lecturer_id = ca2.lecturer_id THEN 'Lecturer Conflict'
        WHEN ca1.program_id = ca2.program_id THEN 'Program Conflict'
        ELSE 'Time Overlap'
    END as conflict_type
FROM TT_APP_timetable t1
JOIN TT_APP_timetable t2 ON 
    t1.day = t2.day AND
    t1.id < t2.id AND
    t1.start_time < t2.end_time AND
    t1.end_time > t2.start_time
JOIN TT_APP_courseallocation ca1 ON t1.course_allocation_id = ca1.id
JOIN TT_APP_courseallocation ca2 ON t2.course_allocation_id = ca2.id;

-- ============================================================
-- 7. FUNCTIONS AND TRIGGERS
-- ============================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply trigger to all tables with updated_at column
CREATE TRIGGER update_faculty_updated_at BEFORE UPDATE ON TT_APP_faculty
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_department_updated_at BEFORE UPDATE ON TT_APP_department
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_program_updated_at BEFORE UPDATE ON TT_APP_program
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_courseallocation_updated_at BEFORE UPDATE ON TT_APP_courseallocation
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Function to archive old timetables
CREATE OR REPLACE FUNCTION archive_old_timetables()
RETURNS void AS $$
BEGIN
    -- Archive timetables older than 2 years
    INSERT INTO TT_APP_timetablearchive (timetable_type, semester, academic_year, archived_by, data)
    SELECT 
        'MAIN',
        EXTRACT(month FROM created_at)::INTEGER / 6 + 1, -- Convert to semester
        CONCAT(EXTRACT(year FROM created_at), '/', EXTRACT(year FROM created_at) + 1),
        1, -- System user
        jsonb_agg(to_jsonb(t))
    FROM TT_APP_timetable t
    WHERE created_at < CURRENT_DATE - INTERVAL '2 years'
    GROUP BY EXTRACT(year FROM created_at), EXTRACT(month FROM created_at)::INTEGER / 6 + 1;
    
    -- Delete archived records
    DELETE FROM TT_APP_timetable 
    WHERE created_at < CURRENT_DATE - INTERVAL '2 years';
END;
$$ language 'plpgsql';

-- ============================================================
-- 8. DATABASE MAINTENANCE
-- ============================================================

-- Vacuum and analyze schedule
-- Run weekly maintenance
-- VACUUM ANALYZE;

-- Update statistics
-- ANALYZE;

-- Check for unused indexes
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan as index_scans
FROM pg_stat_user_indexes 
WHERE idx_scan = 0 
ORDER BY schemaname, tablename, indexname;

-- Check table sizes
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) as total_size,
    pg_size_pretty(pg_relation_size(schemaname || '.' || tablename)) as table_size,
    pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename) - 
                  pg_relation_size(schemaname || '.' || tablename)) as index_size
FROM pg_tables 
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_total_relation_size(schemaname || '.' || tablename) DESC;</code></pre>
''',
    language='sql',
    order=1,
    description='Complete SQL schema for the timetabling system database'
)

# ============================================================
# 6. SUMMARY
# ============================================================

print("\n" + "=" * 80)
print("SYSTEM DESIGN DOCUMENTATION COMPLETE")
print("=" * 80)

# Count statistics
total_pages = DocumentationPage.objects.filter(category__in=categories.values()).count()
total_sections = DocumentationSection.objects.filter(page__category__in=categories.values()).count()
total_code_examples = CodeExample.objects.filter(section__page__category__in=categories.values()).count()
total_tags = DocumentationTag.objects.count()

print(f"""
📊 SYSTEM DESIGN DOCUMENTATION STATISTICS:
   Total Categories Created:      {len(categories)}
   Total Pages Created:           {total_pages}
   Total Sections:                {total_sections}
   Total Code Examples:           {total_code_examples}
   Total Tags:                    {total_tags}

🏗️ COMPREHENSIVE SYSTEM DESIGN CREATED:
   1. System Architecture - Complete Overview
   2. Database Design - Complete Schema Documentation
   3. Entity Relationship Diagrams - Complete ERD Documentation
   4. File Management System - Complete Documentation
   5. Process Flows and Workflows - Complete Documentation

🔧 TECHNICAL DOCUMENTATION INCLUDES:
   • Complete system architecture diagrams
   • Database schema with relationships and constraints
   • Entity Relationship Diagrams (ERD)
   • File management system design
   • Business process flows and workflows
   • Technology stack specifications
   • Deployment architecture
   • Security considerations
   • Performance optimization strategies

💾 DATABASE DESIGN FEATURES:
   • Complete SQL schema with all tables
   • Indexing strategy for optimal performance
   • Foreign key relationships and constraints
   • Views for reporting and analytics
   • Functions and triggers for automation
   • Maintenance procedures

📁 FILE MANAGEMENT FEATURES:
   • Multi-storage backend support (Local, S3, MinIO)
   • Image processing and optimization
   • Secure file upload and download
   • Backup and archival procedures
   • File validation and virus scanning

🔄 PROCESS FLOWS DOCUMENTED:
   • Course allocation workflow
   • Timetable generation process
   • Exam scheduling workflow
   • User management processes
   • Approval workflows with state machines

🔒 SECURITY CONSIDERATIONS:
   • Secure file upload validation
   • Access control for sensitive files
   • Database security best practices
   • API security measures
   • Audit logging for all operations

🚀 DEPLOYMENT READY:
   • Production deployment architecture
   • Scaling strategies documented
   • Monitoring and logging configuration
   • Backup and recovery procedures
   • Maintenance schedules

✅ System design documentation successfully created!
   Access at: /documentation/category/system-architecture/
""")

print("=" * 80)
print("NEXT STEPS FOR SYSTEM IMPLEMENTATION:")
print("1. Review system architecture design")
print("2. Implement database schema from SQL")
print("3. Set up file management system")
print("4. Implement process workflows")
print("5. Configure deployment environment")
print("6. Set up monitoring and logging")
print("7. Implement security measures")
print("8. Test complete system integration")
print("=" * 80)