from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from .models import Course
from .services import CourseService
from apps.configuration.services import ConfigurationService
from integrations.clickfunnels.client import ClickFunnelsClient

@login_required
def course_list(request):
    """
    CRM List View for Courses with search and filtering.
    """
    query = request.GET.get("q", "")
    workspace_filter = request.GET.get("workspace_id", "")
    
    courses = Course.objects.all().annotate(
        enrollment_count=Count("enrollment_attempts")
    ).order_by("name")
    
    if query:
        courses = courses.filter(
            Q(name__icontains=query) | 
            Q(cf_course_id__icontains=query)
        )
    
    if workspace_filter:
        courses = courses.filter(workspace_id=workspace_filter)
        
    paginator = Paginator(courses, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    
    workspace_options = Course.objects.values_list("workspace_id", flat=True).distinct()
    
    headers = [
        {"label": "Course Name", "sortable": True},
        {"label": "Workspace", "sortable": True},
        {"label": "Enrollments", "sortable": True},
        {"label": "Last Updated", "sortable": True},
    ]
    
    context = {
        "page_obj": page_obj,
        "query": query,
        "workspace_filter": workspace_filter,
        "workspace_options": workspace_options,
        "headers": headers,
    }
    return render(request, "courses/list.html", context)

@login_required
def course_detail(request, cf_course_id):
    """
    Detailed course view with recent enrollment attempts.
    """
    course = get_object_or_404(Course, cf_course_id=cf_course_id)
    recent_enrollments = course.enrollment_attempts.all().select_related("contact")[:10]
    
    context = {
        "course": course,
        "recent_enrollments": recent_enrollments,
    }
    return render(request, "courses/detail.html", context)

@login_required
def course_sync(request):
    """
    View to trigger course sync from ClickFunnels.
    Aligns with multi-step config and subdomain requirements.
    """
    config_service = ConfigurationService()
    active_config = config_service.get_active_config()
    
    if request.method == "POST":
        if not active_config:
            messages.error(request, "No active ClickFunnels configuration found.")
            return redirect("courses:sync")
            
        # Validation based on refactored config structure
        if not active_config.workspace_id:
            messages.error(request, "Select a workspace before syncing courses.")
            return redirect("configuration:settings")

        if not active_config.workspace_subdomain:
            messages.error(request, "Workspace subdomain is missing. Workspace-level API calls cannot continue.")
            return redirect("configuration:settings")

        try:
            client = ClickFunnelsClient.from_configuration(active_config)
            service = CourseService(client=client)
            # Use data from active config
            result = service.sync_courses(
                workspace_id=int(active_config.workspace_id),
                workspace_subdomain=active_config.workspace_subdomain
            )
            messages.success(
                request, 
                f"Successfully synced {result['synced_count']} courses "
                f"({result['created_count']} new, {result['updated_count']} updated)."
            )
            return redirect("courses:list")
        except Exception as e:
            messages.error(request, f"Error during course sync: {str(e)}")
            return redirect("courses:sync")

    context = {
        "active_config": active_config,
    }
    return render(request, "courses/sync.html", context)
