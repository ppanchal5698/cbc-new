"""
Role-based access control (§11.2, NFR-10, Risk R5).

Two roles, one axis: ADMIN is a strict superset of ESTIMATOR. What is asserted
here is mostly the *narrowing* — the three things an estimator must not be able to
do, and the confirmation that everything else stayed open, because a control that
also blocks the everyday work gets removed rather than obeyed.
"""

import pytest
from django.contrib.auth import get_user_model
from factories import CatalogItemFactory, MarginBandFactory, ProjectFactory, UserFactory
from rest_framework import status
from rest_framework.test import APIClient

from shared.enums import Role

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def estimator():
    return UserFactory(role=Role.ESTIMATOR.value)


@pytest.fixture
def admin():
    return UserFactory(role=Role.ADMIN.value)


def client_for(user) -> APIClient:
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class TestTheRoleIsTheSourceOfTruth:
    def test_admin_role_grants_admin_site_access(self, admin):
        """`is_staff` is what Django reads to open /admin/, and it follows role."""
        assert admin.is_staff is True
        assert admin.is_admin is True

    def test_estimator_role_does_not(self, estimator):
        assert estimator.is_staff is False
        assert estimator.is_admin is False

    def test_is_staff_cannot_drift_from_role(self, estimator):
        """
        Two writable sources for one fact drift. Someone ticks the admin-site box
        without changing the role, and a user holds admin-site access while every
        API permission check still says estimator.
        """
        estimator.is_staff = True
        estimator.save()
        estimator.refresh_from_db()

        assert estimator.is_staff is False, "role is the source of truth, not is_staff"

    def test_promoting_the_role_grants_the_site(self, estimator):
        estimator.role = Role.ADMIN.value
        estimator.save()
        estimator.refresh_from_db()
        assert estimator.is_staff is True

    def test_a_new_signup_is_an_estimator(self):
        user = User.objects.create_user(email="new@cbc.test", password="x")
        assert user.role == Role.ESTIMATOR.value
        assert user.is_staff is False

    def test_a_superuser_is_an_admin(self):
        admin = User.objects.create_superuser(email="root@cbc.test", password="x")
        assert admin.role == Role.ADMIN.value
        assert admin.is_staff is True


class TestReferenceDataIsAdminOnlyToWrite:
    """
    Margin bands, vendor multipliers and the catalog are CBC's commercial data.
    Until this existed, any account could rewrite a margin — and a quote priced
    from a silently altered multiplier is wrong in a way nothing downstream
    detects.
    """

    @pytest.mark.parametrize(
        "path", ["/api/margin-bands/", "/api/vendor-multipliers/", "/api/tax-rates/",
                 "/api/finish-codes/", "/api/throat-depths/", "/api/catalog-items/"]
    )
    def test_an_estimator_can_read(self, estimator, path):
        """Reads stay open. An estimator cannot price a door without them."""
        assert client_for(estimator).get(path).status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        "path", ["/api/margin-bands/", "/api/vendor-multipliers/", "/api/tax-rates/",
                 "/api/finish-codes/", "/api/throat-depths/", "/api/catalog-items/"]
    )
    def test_an_estimator_cannot_write(self, estimator, path):
        response = client_for(estimator).post(path, {}, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_estimator_cannot_edit_an_existing_margin(self, estimator):
        band = MarginBandFactory()
        response = client_for(estimator).patch(
            f"/api/margin-bands/{band.id}/", {"margin_pct": "0.99"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_admin_can_edit_a_margin(self, admin):
        band = MarginBandFactory()
        response = client_for(admin).patch(
            f"/api/margin-bands/{band.id}/", {"margin_pct": "0.42"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK

    def test_an_admin_can_add_a_catalog_item(self, admin):
        item = CatalogItemFactory()
        response = client_for(admin).patch(
            f"/api/catalog-items/{item.id}/", {"description": "revised"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK


class TestDeletionIsAdminOnly:
    """
    Deleting a project or quote removes what a citation, an audit trail and a sent
    price hang off. Estimators still create, edit and approve.
    """

    def test_an_estimator_cannot_delete_a_project(self, estimator):
        project = ProjectFactory()
        response = client_for(estimator).delete(f"/api/projects/{project.id}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_admin_can_delete_a_project(self, admin):
        project = ProjectFactory()
        response = client_for(admin).delete(f"/api/projects/{project.id}/")
        assert response.status_code in (
            status.HTTP_204_NO_CONTENT, status.HTTP_200_OK,
        )

    def test_an_estimator_can_still_create_and_edit_a_project(self, estimator):
        """The everyday work must stay frictionless, or the control gets removed."""
        api = client_for(estimator)
        created = api.post(
            "/api/projects/",
            {"name": "New Bid", "initiator_email": "e@cbc.test"},
            format="json",
        )
        assert created.status_code == status.HTTP_201_CREATED

        edited = api.patch(
            f"/api/projects/{created.data['id']}/", {"name": "Renamed Bid"}, format="json"
        )
        assert edited.status_code == status.HTTP_200_OK


class TestEstimatorsKeepTheirWork:
    """
    RBAC narrows three things and nothing else. If any of these start failing, the
    roles have been drawn too tightly.
    """

    @pytest.mark.parametrize(
        "path",
        ["/api/projects/", "/api/documents/", "/api/openings/", "/api/quotes/",
         "/api/quote-lines/", "/api/matches/", "/api/feedback/", "/api/provenance/"],
    )
    def test_an_estimator_can_read_the_working_surface(self, estimator, path):
        assert client_for(estimator).get(path).status_code == status.HTTP_200_OK

    def test_quote_approval_is_not_admin_only(self, estimator):
        """
        NFR-1 says "no quote sent without explicit ESTIMATOR approval", and NFR-9 —
        approval authority and dollar thresholds — is out of scope. Restricting
        approval to admins would contradict both.
        """
        from common.permissions import IsAdmin
        from quotes.views import QuoteViewSet

        assert IsAdmin not in QuoteViewSet.permission_classes


class TestTheProfileExposesRoleWithoutOfferingIt:
    def test_me_reports_the_role(self, estimator):
        response = client_for(estimator).get("/api/auth/me/")
        assert response.data["role"] == Role.ESTIMATOR.value

    def test_a_patch_cannot_promote(self, estimator):
        """The escalation case for roles, alongside the one for is_staff."""
        response = client_for(estimator).patch(
            "/api/auth/me/", {"role": Role.ADMIN.value}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK

        estimator.refresh_from_db()
        assert estimator.role == Role.ESTIMATOR.value
        assert estimator.is_staff is False
