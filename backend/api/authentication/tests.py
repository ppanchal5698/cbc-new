"""
Access control (§11.2, NFR-4, C3 / ADR-0004).

    **Django auth is the system of record.** Cognito is removed from the
    near-term stack. It solves a problem this build does not have — 10 known
    internal users, already authenticated — and adding it now imports a
    token-exchange integration into the highest-risk phase. If SSO is later
    required, Cognito or Entra ID sits *in front of* Django as an OIDC provider;
    Django remains the authorisation and audit boundary.

This file previously asserted the opposite of everything below. It was a suite of
tests named ``test_unauthenticated_read_returns_200_not_401`` that documented the
missing auth layer as though it were the specification, so the build stayed green
while every endpoint served customer drawings and pricing to anyone who asked.
"""

import pytest
from rest_framework import status

pytestmark = pytest.mark.django_db

#: Every list endpoint. All of them read customer drawings, pricing, or both.
PROTECTED_ENDPOINTS = [
    "/api/projects/",
    "/api/documents/",
    "/api/manifest/",
    "/api/pipeline-jobs/",
    "/api/bid-alternates/",
    "/api/openings/",
    "/api/doc-elements/",
    "/api/extraction-runs/",
    "/api/provenance/",
    "/api/matches/",
    "/api/catalog-items/",
    "/api/finish-codes/",
    "/api/throat-depths/",
    "/api/margin-bands/",
    "/api/vendor-multipliers/",
    "/api/tax-rates/",
    "/api/quotes/",
    "/api/quote-lines/",
    "/api/vendor-rfqs/",
    "/api/feedback/",
    "/api/extraction-metrics/",
]

DENIED = (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


class TestEveryEndpointIsProtected:
    @pytest.mark.parametrize("path", PROTECTED_ENDPOINTS)
    def test_unauthenticated_read_is_refused(self, api_client, path):
        assert api_client.get(path).status_code in DENIED, f"{path} is readable anonymously"

    @pytest.mark.parametrize("path", PROTECTED_ENDPOINTS)
    def test_unauthenticated_write_is_refused(self, api_client, path):
        assert api_client.post(path, {}, format="json").status_code in DENIED

    def test_unauthenticated_upload_is_refused(self, api_client):
        """The intake path is the most sensitive endpoint in the system."""
        from factories import ProjectFactory

        project = ProjectFactory()
        response = api_client.post(f"/api/projects/{project.id}/documents/", {}, format="multipart")
        assert response.status_code in DENIED

    def test_unauthenticated_approval_is_refused(self, api_client):
        """NFR-1: nothing is approved without an identified estimator."""
        from factories import QuoteFactory

        quote = QuoteFactory()
        response = api_client.post(
            f"/api/quotes/{quote.id}/approve/", {"confirm": True}, format="json"
        )
        assert response.status_code in DENIED
        quote.refresh_from_db()
        assert quote.status == "DRAFT"

    def test_no_endpoint_was_missed(self, auth_client):
        """
        Guard against a new router being added without a protection test.

        Compares the list above against what the URL conf actually registers, so
        adding an endpoint and forgetting this file fails the build.
        """
        from django.urls import get_resolver

        registered = {
            f"/{pattern.pattern}"
            for pattern in get_resolver().url_patterns
            if hasattr(pattern, "url_patterns")
        }
        assert registered is not None  # resolver loaded; the real check is below

        listed = set(PROTECTED_ENDPOINTS)
        response = auth_client.get("/api/schema/")
        assert response.status_code == status.HTTP_200_OK
        schema_paths = {
            path
            for path in response.data["paths"]
            if path.count("/") == 3 and not path.endswith("}/") and "schema" not in path
        }
        missing = schema_paths - listed - {"/api/health/"}
        assert not missing, f"these list endpoints have no protection test: {sorted(missing)}"


class TestHealthIsIntentionallyOpen:
    def test_health_needs_no_credentials(self, api_client):
        """The load balancer and container healthcheck cannot authenticate."""
        response = api_client.get("/api/health/")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "ok"

    def test_health_reveals_nothing_useful_to_an_attacker(self, api_client):
        body = api_client.get("/api/health/").data
        assert set(body) == {"status", "database", "environment"}
        assert "version" not in body and "hostname" not in body


class TestAuthenticatedAccess:
    def test_a_logged_in_estimator_can_read(self, auth_client):
        assert auth_client.get("/api/projects/").status_code == status.HTTP_200_OK

    def test_django_auth_is_the_boundary_not_cognito(self):
        """
        ADR-0004.

        No Cognito, no token-exchange dependency, no second identity source. If
        this assertion ever fails, the decision was reversed without an ADR.
        """
        from django.conf import settings

        assert "django.contrib.auth" in settings.INSTALLED_APPS
        assert not any("cognito" in app.lower() for app in settings.INSTALLED_APPS)

    def test_authentication_is_required_by_default_not_per_view(self):
        """
        A permission default of AllowAny plus per-view opt-in is how endpoints end
        up public by omission. The default denies; opting out must be deliberate.
        """
        from django.conf import settings

        assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
            "rest_framework.permissions.IsAuthenticated"
        ]
