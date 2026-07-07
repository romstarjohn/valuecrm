from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.enrollments.models import EnrollmentAttempt
from apps.configuration.models import ClickFunnelsConfig

@login_required
def index(request):
    """
    Polished CRM Dashboard Upgrade.
    Displays KPIs, integration status, and recent activity.
    """
    # 1. KPI Counts
    total_contacts = Contact.objects.count()
    total_courses = Course.objects.count()
    attempts = EnrollmentAttempt.objects.all()
    total_attempts = attempts.count()
    success_enrollments = attempts.filter(status=EnrollmentAttempt.Status.SUCCESS).count()
    failed_enrollments = attempts.filter(status=EnrollmentAttempt.Status.FAILURE).count()

    # 2. Integration Status
    active_config = ClickFunnelsConfig.objects.filter(is_active=True).first()

    # 3. Recent Activity (Latest 5)
    recent_contacts = Contact.objects.all()[:5]
    recent_enrollments = EnrollmentAttempt.objects.all().select_related("contact", "course")[:5]

    # 4. Failed Alert Section (Latest 3 failures)
    recent_failures = attempts.filter(status=EnrollmentAttempt.Status.FAILURE).select_related("contact", "course")[:3]

    context = {
        "total_contacts": total_contacts,
        "total_courses": total_courses,
        "total_attempts": total_attempts,
        "success_enrollments": success_enrollments,
        "failed_enrollments": failed_enrollments,
        "active_config": active_config,
        "recent_contacts": recent_contacts,
        "recent_enrollments": recent_enrollments,
        "recent_failures": recent_failures,
    }
    return render(request, "dashboard/index.html", context)

@login_required
def table_demo(request):
    """
    Demo view for CRM Data Table components.
    """
    dummy_data = [
        {"id": 1, "name": "John Doe", "email": "john@example.com", "status": "active"},
        {"id": 2, "name": "Jane Smith", "email": "jane@example.com", "status": "pending"},
        {"id": 3, "name": "Bob Brown", "email": "bob@example.com", "status": "inactive"},
        {"id": 4, "name": "Alice Green", "email": "alice@example.com", "status": "active"},
    ]
    
    headers = [
        {"label": "Name", "sortable": True},
        {"label": "Email", "sortable": True},
        {"label": "Status", "sortable": False},
    ]
    
    context = {
        "dummy_data": dummy_data,
        "headers": headers,
        "page_obj": {
            "has_other_pages": True,
            "has_previous": False,
            "has_next": True,
            "number": 1,
            "previous_page_number": 0,
            "next_page_number": 2,
            "start_index": 1,
            "end_index": 4,
            "paginator": {"count": 10, "page_range": range(1, 4)}
        }
    }
    return render(request, "dashboard/table_demo.html", context)
