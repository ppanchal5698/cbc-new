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
    # Authenticated auth routes. Signing in is public; everything you do once
    # signed in is not.
    "/api/auth/logout/",
    "/api/auth/me/",
    "/api/auth/change-password/",
]

#: Custom actions hanging off a detail route.
#:
#: These were invisible to the old coverage check, which filtered on path depth —
#: and they are the endpoints that *do* things: approve a quote, force-read a
#: skipped page, override a provenance value, upload a document. Probed with a nil
#: UUID because DRF authenticates before it resolves an object, so an anonymous
#: caller is refused rather than told whether the id exists.
DETAIL_ACTION_ENDPOINTS = [
    "/api/projects/{id}/documents/",
    "/api/documents/{id}/manifest/",
    "/api/documents/{id}/page-diffs/",
    "/api/documents/{id}/pipeline-jobs/",
    "/api/manifest/{id}/force-read/",
    "/api/openings/{id}/matches/",
    "/api/openings/{id}/needs-review/",
    "/api/matches/{id}/accept/",
    "/api/matches/{id}/reject/",
    "/api/provenance/{id}/override/",
    "/api/provenance/{id}/source/",
    "/api/quotes/search/",
    "/api/quotes/{id}/approve/",
    "/api/quotes/{id}/export/",
    "/api/quotes/{id}/recalculate/",
    "/api/vendor-rfqs/{id}/record-price/",
]

NIL_UUID = "00000000-0000-0000-0000-000000000000"

#: The only endpoints an anonymous caller may reach, and why.
#:
#: Asserted as an exact set below rather than a minimum. The previous coverage
#: check filtered schema paths on ``path.count("/") == 3``, which quietly excluded
#: everything under ``/api/auth/`` — so a new *public* endpoint, the exact thing
#: this file exists to catch, slipped through on path depth alone.
PUBLIC_ENDPOINTS = {
    "/api/health/",            # the load balancer cannot authenticate
    "/api/auth/signup/",       # requesting access, which grants none
    "/api/auth/login/",        # the door
    "/api/auth/token/",        # the same door, for scripts
    # Reset is reachable precisely by people who cannot authenticate. It grants
    # nothing on its own: the request only mails a link to an address already on
    # file, and the confirm endpoint needs a token derived from the current
    # password hash.
    "/api/auth/password-reset/",
    "/api/auth/password-reset/confirm/",
}

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

        # Every non-detail path the API publishes. No depth filter: an endpoint
        # nested one segment deeper is not thereby less sensitive, and the old
        # `count("/") == 3` rule excluded all of /api/auth/ by accident.
        schema_paths = {
            path
            for path in response.data["paths"]
            if not path.endswith("}/") and "schema" not in path
        }
        covered = listed | set(DETAIL_ACTION_ENDPOINTS) | PUBLIC_ENDPOINTS
        missing = schema_paths - covered
        assert not missing, f"these endpoints have no protection test: {sorted(missing)}"

    @pytest.mark.parametrize("template", DETAIL_ACTION_ENDPOINTS)
    def test_detail_actions_refuse_anonymous_callers(self, api_client, template):
        """
        The endpoints that act rather than read. DRF authenticates before it
        resolves the object, so a refusal here is about credentials and not about
        whether the id happens to exist.
        """
        path = template.format(id=NIL_UUID)
        assert api_client.get(path).status_code in DENIED
        assert api_client.post(path, {}, format="json").status_code in DENIED

    def test_the_public_surface_is_exactly_what_we_expect(self, api_client):
        """
        Anonymous reachability, asserted as an exact set.

        Making an endpoint public should require editing PUBLIC_ENDPOINTS, which is
        a decision someone reviews — not adding a view with AllowAny and hoping a
        path-depth filter notices.
        """
        for path in sorted(PUBLIC_ENDPOINTS):
            response = api_client.get(path)
            assert response.status_code not in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            ), f"{path} is listed public but refuses anonymous callers"


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


class TestThrottlingIsConfigured:
    def test_the_anonymous_endpoints_carry_a_rate(self, settings):
        """
        A public login endpoint with no ceiling is an open brute-force target. The
        rates themselves are a judgement call; their existence is not.

        Asserted here rather than beside the login tests, because those null the
        rates out to stay deterministic — and a test that checks a value its own
        fixture just erased proves nothing.
        """
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        for scope in ("login", "signup", "token", "password"):
            assert rates.get(scope), f"{scope} has no throttle rate"
