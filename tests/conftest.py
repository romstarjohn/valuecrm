import pytest
from django.contrib.auth.models import User
from django.test import Client

@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    pass

@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username="staff", 
        password="password", 
        is_staff=True
    )

@pytest.fixture
def staff_client(staff_user):
    client = Client()
    client.login(username="staff", password="password")
    return client
