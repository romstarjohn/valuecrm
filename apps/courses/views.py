from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from .models import Course
from .services import CourseService
from apps.configuration.services import ConfigurationService
from integrations.clickfunnels.client import ClickFunnelsClient

COURSE_SORT_FIELDS = {
    "name": ["name"],
    "workspace": ["workspace_id"],
    "enrollments": ["enrollment_count"],
    "updated": ["updated_at"],
}

def _querystring(request, *, exclude=()):
    """Current GET params, minus `exclude`, for building links that keep
    every other active filter/sort param (pagination, sort-column links)."""
    params = request.GET.copy()
    for key in exclude:
        params.pop(key, None)
    return params.urlencode()

def _filter_options(request, *, param, options, all_label):
    """(label, url, active) rows for a single-select querystring filter,
    preserving every other active filter/sort param and resetting to page 1."""
    current = request.GET.get(param, "")
    base_params = request.GET.copy()
    base_params.pop("page", None)

    all_params = base_params.copy()
    all_params.pop(param, None)
    rows = [{"label": all_label, "url": "?" + all_params.urlencode(), "active": not current}]

    for value in options:
        opt_params = base_params.copy()
        opt_params[param] = value
        rows.append({"label": value, "url": "?" + opt_params.urlencode(), "active": current == value})
    return rows

@login_required
def course_list(request):
    """
    CRM List View for Courses with search and filtering.
    """
    query = request.GET.get("q", "")
    workspace_filter = request.GET.get("workspace_id", "")

    courses = Course.objects.all().annotate(
        enrollment_count=Count("enrollment_attempts")
    )

    if query:
        courses = courses.filter(
            Q(name__icontains=query) |
            Q(cf_course_id__icontains=query)
        )

    if workspace_filter:
        courses = courses.filter(workspace_id=workspace_filter)

    sort_key = request.GET.get("sort", "")
    sort_dir = request.GET.get("dir", "asc")
    if sort_key in COURSE_SORT_FIELDS:
        order_fields = COURSE_SORT_FIELDS[sort_key]
        if sort_dir == "desc":
            order_fields = [f"-{field}" for field in order_fields]
        courses = courses.order_by(*order_fields)
    else:
        sort_key = ""
        sort_dir = ""
        courses = courses.order_by("name")

    paginator = Paginator(courses, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    workspace_options = list(Course.objects.values_list("workspace_id", flat=True).distinct())

    headers = [
        {"label": "Course Name", "sortable": True, "sort_key": "name"},
        {"label": "Workspace", "sortable": True, "sort_key": "workspace"},
        {"label": "Enrollments", "sortable": True, "sort_key": "enrollments"},
        {"label": "Last Updated", "sortable": True, "sort_key": "updated"},
    ]

    context = {
        "page_obj": page_obj,
        "query": query,
        "workspace_filter": workspace_filter,
        "workspace_options": workspace_options,
        "workspace_filter_options": _filter_options(request, param="workspace_id", options=workspace_options, all_label="All Workspaces"),
        "workspace_label": f"Workspace: {workspace_filter or 'All'}",
        "headers": headers,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "sort_qs": _querystring(request, exclude=("page", "sort", "dir")),
        "querystring": _querystring(request, exclude=("page",)),
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
