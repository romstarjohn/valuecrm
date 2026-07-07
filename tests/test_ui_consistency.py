import pytest
from django.urls import reverse
from apps.courses.models import Course
from apps.contacts.models import Contact
from apps.enrollments.models import EnrollmentAttempt

@pytest.mark.django_db
def test_courses_list_layout_consistency(staff_client):
    """
    Regression test for Courses list visual layout.
    Ensures exactly one table container and correct column alignment.
    """
    Course.objects.create(cf_course_id="331", name="Test Course", workspace_id="hammer")
    url = reverse("courses:list")
    response = staff_client.get(url)
    content = response.content.decode()
    
    # 1. Check for single table container (no nesting)
    assert content.count('class="table-container') == 1
    
    # 2. Check for exactly one table and tbody
    assert content.count('<table') == 1
    assert content.count('<tbody') == 1
    
    # 3. Check column count (5 columns: Name, Workspace, Enrollments, Updated At, Actions)
    # Each <tr> should have 5 <td> elements because selectable=False
    assert content.count('<td') == 5

@pytest.mark.django_db
def test_contacts_list_layout_consistency(staff_client):
    """
    Regression test for Contacts list layout.
    """
    Contact.objects.create(email="test@example.com", first_name="John", last_name="Doe")
    url = reverse("contacts:list")
    response = staff_client.get(url)
    content = response.content.decode()
    
    assert content.count('class="table-container') == 1
    # 6 columns: Checkbox, Name, Status, Tags, Source, Actions
    assert content.count('<td') == 6

@pytest.mark.django_db
def test_enrollments_list_layout_consistency(staff_client):
    """
    Regression test for Enrollments list layout.
    """
    contact = Contact.objects.create(email="test@example.com")
    course = Course.objects.create(cf_course_id="1", name="C1", workspace_id="w1")
    EnrollmentAttempt.objects.create(contact=contact, course=course, status="SUCCESS")
    
    url = reverse("enrollments:list")
    response = staff_client.get(url)
    content = response.content.decode()
    
    assert content.count('class="table-container') == 1
    # 5 columns: Student, Course, Status, ID, Date, Actions? 
    # Check headers in enrollment_list: Student, Course, Status, ClickFunnels ID, Date + Actions = 6
    assert content.count('<td') == 6
