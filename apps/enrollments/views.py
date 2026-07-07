from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from .models import EnrollmentAttempt
from .forms import EnrollmentForm, BulkEnrollmentForm
from .services import EnrollmentService
from apps.contacts.services import ContactService
from apps.configuration.services import ConfigurationService
from apps.courses.models import Course
from integrations.clickfunnels.client import ClickFunnelsClient

def _get_enrollment_service():
    config_service = ConfigurationService()
    config = config_service.get_active_config()
    if not config:
        return None, None
    
    client = ClickFunnelsClient.from_configuration(config)
    contact_service = ContactService(client=client)
    return EnrollmentService(client=client, contact_service=contact_service), config

@login_required
def enrollment_list(request):
    """
    CRM List View for Enrollment History.
    """
    query = request.GET.get("q", "")
    status_filter = request.GET.get("status", "")
    course_filter = request.GET.get("course_id", "")
    
    attempts = EnrollmentAttempt.objects.all().select_related("contact", "course")
    
    if query:
        attempts = attempts.filter(
            Q(contact__email__icontains=query) | 
            Q(course__name__icontains=query)
        )
    
    if status_filter:
        attempts = attempts.filter(status=status_filter)
    
    if course_filter:
        attempts = attempts.filter(course__cf_course_id=course_filter)
        
    paginator = Paginator(attempts, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    
    status_options = EnrollmentAttempt.Status.choices
    course_options = Course.objects.all().order_by("name")
    
    headers = [
        {"label": "Student", "sortable": True},
        {"label": "Course", "sortable": True},
        {"label": "Status", "sortable": True},
        {"label": "ClickFunnels ID", "sortable": False},
        {"label": "Date", "sortable": True},
    ]
    
    context = {
        "page_obj": page_obj,
        "query": query,
        "status_filter": status_filter,
        "course_filter": course_filter,
        "status_options": status_options,
        "course_options": course_options,
        "headers": headers,
    }
    return render(request, "enrollments/list.html", context)

@login_required
def enrollment_new(request):
    """
    Single enrollment trigger view.
    """
    service, config = _get_enrollment_service()
    
    if request.method == "POST":
        form = EnrollmentForm(request.POST)
        if form.is_valid():
            if not service:
                messages.error(request, "No active ClickFunnels configuration found.")
                return redirect("enrollments:new")
                
            email = form.cleaned_data["email"]
            course_id = form.cleaned_data["course_id"]
            
            # Validation
            if not config.workspace_id or not config.workspace_subdomain:
                messages.error(request, "Select a workspace in settings before enrolling contacts.")
                return redirect("configuration:settings")

            try:
                result_dto = service.enroll_contact(
                    workspace_subdomain=config.workspace_subdomain,
                    workspace_id=int(config.workspace_id),
                    email=email, 
                    cf_course_id=course_id
                )
                if result_dto.status == EnrollmentAttempt.Status.SUCCESS:
                    messages.success(request, f"Successfully enrolled {email} in course.")
                    return redirect("enrollments:detail", pk=result_dto.enrollment_attempt_id)
                else:
                    messages.error(request, f"Enrollment failed: {result_dto.error_message}")
                    if result_dto.enrollment_attempt_id:
                        return redirect("enrollments:detail", pk=result_dto.enrollment_attempt_id)
            except Exception as e:
                messages.error(request, f"Unexpected error: {str(e)}")
                
    else:
        # Pre-fill from query params if available (e.g. from contact detail)
        initial = {
            "email": request.GET.get("email", ""),
            "course_id": request.GET.get("course_id", "")
        }
        form = EnrollmentForm(initial=initial)
        
    return render(request, "enrollments/single.html", {"form": form, "config": config})

@login_required
def enrollment_bulk(request):
    """
    Bulk enrollment view with result summary.
    """
    service, config = _get_enrollment_service()
    bulk_result = None
    
    if request.method == "POST":
        form = BulkEnrollmentForm(request.POST)
        if form.is_valid():
            if not service:
                messages.error(request, "No active ClickFunnels configuration found.")
                return redirect("enrollments:bulk")
                
            emails = form.cleaned_data["emails"]
            course_id = form.cleaned_data["course_id"]
            
            # Validation
            if not config.workspace_id or not config.workspace_subdomain:
                messages.error(request, "Select a workspace in settings before enrolling contacts.")
                return redirect("configuration:settings")

            try:
                bulk_result = service.bulk_enroll(
                    workspace_subdomain=config.workspace_subdomain,
                    workspace_id=int(config.workspace_id),
                    emails=emails, 
                    cf_course_id=course_id
                )
                messages.info(request, f"Bulk enrollment processed: {bulk_result['success_count']} successes, {bulk_result['failure_count']} failures.")
            except Exception as e:
                messages.error(request, f"Unexpected error: {str(e)}")
    else:
        form = BulkEnrollmentForm()
        
    context = {
        "form": form,
        "config": config,
        "bulk_result": bulk_result
    }
    return render(request, "enrollments/bulk.html", context)

@login_required
def enrollment_detail(request, pk):
    """
    Detailed audit view for an enrollment attempt.
    """
    attempt = get_object_or_404(EnrollmentAttempt.objects.select_related("contact", "course"), pk=pk)
    
    context = {
        "attempt": attempt,
    }
    return render(request, "enrollments/detail.html", context)
