"""
Shared pytest fixtures for all Django API tests.
Placed at the api/ root so pytest-django discovers it before any app tests.
"""
import pytest
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.create_user(
        username="testuser", password="testpass123", email="test@cbc.test"
    )


@pytest.fixture
def auth_client(api_client, user):
    """APIClient with force_authenticate — bypasses the missing JWT layer."""
    api_client.force_authenticate(user=user)
    return api_client
