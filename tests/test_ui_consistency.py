import re
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

    # 1. Check for single table wrapper (no nesting)
    assert content.count('class="table-wrap') == 1

    # 2. Check for exactly one table and tbody
    assert content.count('<table') == 1
    assert content.count('<tbody') == 1

    # 3. Check column count (5 columns: Offre, Page de vente, Prix, Lien de paiement, État)
    assert content.count('<th>') == 5
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

    assert content.count('class="table-wrap') == 1
    # One row, one cell per header: Client, Téléphone, Ventes, Déjà payé, Accès, Dernière activité
    assert len(re.findall(r"<th[\s>]", content)) == 6
    assert len(re.findall(r"<td[\s>]", content)) == 6

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
    
    assert content.count('class="table-wrap') == 1
    # One row, 5 columns: Client, Formation, Accès, Depuis, action
    assert content.count('<td') == 5
