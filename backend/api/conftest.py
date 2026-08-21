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
    # is_active is explicit: create_user defaults it to False so that a forgotten
    # flag produces an account that cannot sign in rather than one that silently
    # can. A test fixture is one of the few places that genuinely wants True.
    return get_user_model().objects.create_user(
        email="test@cbc.test", password="testpass123", is_active=True
    )


@pytest.fixture
def auth_client(api_client, user):
    """APIClient with force_authenticate — bypasses the missing JWT layer."""
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def admin_user(db):
    """
    An ADMIN. Saving derives is_staff from the role, so the admin site works too.
    """
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        email="admin@cbc.test", password="testpass123", is_active=True, role="ADMIN"
    )


@pytest.fixture
def admin_client(api_client, admin_user):
    """
    For the three things only an admin may do: write reference data, activate a
    user, delete a project or quote. Everything else should use ``auth_client`` —
    a test that reaches for admin where an estimator would do is a test that stops
    noticing when the everyday path breaks.
    """
    api_client.force_authenticate(user=admin_user)
    return api_client
