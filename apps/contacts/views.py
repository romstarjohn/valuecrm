from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from apps.provisioning.models import ProvisioningRequest
from .models import Contact
from .forms import ContactForm

# Column -> model field(s) a list-page column may be sorted by. Whitelisted
# so `?sort=` can never reach arbitrary/relation fields.
CONTACT_SORT_FIELDS = {
    "name": ["first_name", "last_name"],
    "status": ["status"],
    "source": ["source"],
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
def contact_list(request):
    """
    CRM List View for Contacts with search, filtering and sorting.
    """
    query = request.GET.get("q", "")
    status_filter = request.GET.get("status", "")
    source_filter = request.GET.get("source", "")

    contacts = Contact.objects.all()

    if query:
        contacts = contacts.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(email__icontains=query)
        )

    if status_filter:
        contacts = contacts.filter(status=status_filter)

    if source_filter:
        contacts = contacts.filter(source=source_filter)

    sort_key = request.GET.get("sort", "")
    sort_dir = request.GET.get("dir", "asc")
    if sort_key in CONTACT_SORT_FIELDS:
        order_fields = CONTACT_SORT_FIELDS[sort_key]
        if sort_dir == "desc":
            order_fields = [f"-{field}" for field in order_fields]
        contacts = contacts.order_by(*order_fields)
    else:
        sort_key = ""
        sort_dir = ""

    paginator = Paginator(contacts, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Options for filters
    status_options = Contact.objects.values_list("status", flat=True).distinct()
    source_options = Contact.objects.values_list("source", flat=True).distinct()

    headers = [
        {"label": "Contact", "sortable": True, "sort_key": "name"},
        {"label": "Status", "sortable": True, "sort_key": "status"},
        {"label": "Tags", "sortable": False},
        {"label": "Source", "sortable": True, "sort_key": "source"},
    ]

    context = {
        "page_obj": page_obj,
        "query": query,
        "status_filter": status_filter,
        "source_filter": source_filter,
        "status_options": status_options,
        "source_options": source_options,
        "status_filter_options": _filter_options(request, param="status", options=status_options, all_label="All Statuses"),
        "source_filter_options": _filter_options(request, param="source", options=source_options, all_label="All Sources"),
        "status_label": f"Status: {status_filter or 'All'}",
        "source_label": f"Source: {source_filter or 'All'}",
        "headers": headers,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "sort_qs": _querystring(request, exclude=("page", "sort", "dir")),
        "querystring": _querystring(request, exclude=("page",)),
    }
    return render(request, "contacts/list.html", context)

@login_required
def contact_detail(request, pk):
    """
    Detailed contact profile view.
    """
    contact = get_object_or_404(Contact, pk=pk)
    enrollments = list(contact.enrollment_attempts.all().select_related("course"))

    # Link each enrollment row to its matching payments Order (if any), so
    # staff can jump to the operations hub for order-level actions (e.g.
    # Cancel Order) that have no equivalent here. Freeze/resume themselves
    # work directly off EnrollmentAttempt and don't need this lookup.
    order_refs = dict(
        ProvisioningRequest.objects.filter(
            contact_id=contact.pk, course_id__in=[e.course_id for e in enrollments],
        )
        .exclude(status=ProvisioningRequest.Status.CANCELLED)
        .values_list("course_id", "order__reference")
    )
    for enr in enrollments:
        enr.order_reference = order_refs.get(enr.course_id)

    context = {
        "contact": contact,
        "enrollments": enrollments,
        "can_view_order": request.user.has_perm("payments.view_order"),
        "can_freeze_enrollment": request.user.has_perm("payments.freeze_enrollment"),
        "can_resume_enrollment": request.user.has_perm("payments.resume_enrollment"),
    }
    return render(request, "contacts/detail.html", context)

@login_required
def contact_create(request):
    """
    View to add a new contact.
    """
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            contact = form.save()
            messages.success(request, f"Contact {contact.email} created successfully.")
            return redirect("contacts:detail", pk=contact.pk)
    else:
        form = ContactForm()
        
    return render(request, "contacts/form.html", {"form": form, "title": "Add New Contact"})

@login_required
def contact_update(request, pk):
    """
    View to edit an existing contact.
    """
    contact = get_object_or_404(Contact, pk=pk)
    if request.method == "POST":
        form = ContactForm(request.POST, instance=contact)
        if form.is_valid():
            contact = form.save()
            messages.success(request, f"Contact {contact.email} updated successfully.")
            return redirect("contacts:detail", pk=contact.pk)
    else:
        form = ContactForm(instance=contact)
        
    return render(request, "contacts/form.html", {"form": form, "contact": contact, "title": f"Edit {contact.email}"})
