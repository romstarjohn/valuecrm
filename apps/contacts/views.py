from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from .models import Contact
from .forms import ContactForm

@login_required
def contact_list(request):
    """
    CRM List View for Contacts with search and filtering.
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
        
    paginator = Paginator(contacts, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    
    # Options for filters
    status_options = Contact.objects.values_list("status", flat=True).distinct()
    source_options = Contact.objects.values_list("source", flat=True).distinct()
    
    headers = [
        {"label": "Contact", "sortable": True},
        {"label": "Status", "sortable": True},
        {"label": "Tags", "sortable": False},
        {"label": "Source", "sortable": True},
    ]
    
    context = {
        "page_obj": page_obj,
        "query": query,
        "status_filter": status_filter,
        "source_filter": source_filter,
        "status_options": status_options,
        "source_options": source_options,
        "headers": headers,
    }
    return render(request, "contacts/list.html", context)

@login_required
def contact_detail(request, pk):
    """
    Detailed contact profile view.
    """
    contact = get_object_or_404(Contact, pk=pk)
    enrollments = contact.enrollment_attempts.all().select_related("course")
    
    context = {
        "contact": contact,
        "enrollments": enrollments,
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
