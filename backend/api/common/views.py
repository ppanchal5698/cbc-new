"""Operational endpoints (§11.5)."""

from django.db import connection
from django.http import HttpResponseForbidden
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from shared.config import get_settings


@extend_schema(
    summary="Liveness and readiness probe",
    description=(
        "Reports whether the API can reach its database. Used by the container "
        "healthcheck and by the load balancer."
    ),
    responses={200: dict, 503: dict},
    auth=[],
)
@api_view(["GET"])
# The only unauthenticated endpoint in the project, and it deliberately reveals
# nothing beyond liveness: no version, no hostname, no configuration.
@authentication_classes([])
@permission_classes([])
# Also how the browser client obtains its CSRF token: it is the one endpoint a
# signed-out page can call, and every later unsafe request needs the cookie.
@ensure_csrf_cookie
def health(request):
    settings_obj = get_settings()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        database_ok = True
    except Exception:
        database_ok = False

    payload = {"status": "ok" if database_ok else "degraded", "database": database_ok,
               "environment": settings_obj.environment}
    code = status.HTTP_200_OK if database_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return Response(payload, status=code)


class ScalarDocsView(TemplateView):
    """
    Scalar over the live OpenAPI schema, for trying endpoints from a browser.

    A second *reader* of the drf-spectacular document, never a second contract:
    §8.2 keeps the schema generated from the code, and this page fetches the same
    ``/api/schema/`` everything else does.

    Protected exactly as the schema is — public in local development, signed-in
    only once deployed. The schema names every endpoint, every field and every
    enum in the system; publishing that anonymously would sit oddly beside an API
    that will not confirm whether an email address has an account.
    """

    template_name = "scalar.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "schema_url": reverse("schema")}

    def dispatch(self, request, *args, **kwargs):
        if not _docs_are_public() and not request.user.is_authenticated:
            return HttpResponseForbidden(
                "Sign in first — /admin/ for an admin session, or POST "
                "/api/auth/token/ for a token."
            )
        return super().dispatch(request, *args, **kwargs)


def _docs_are_public() -> bool:
    """
    True only in local development.

    Kept as a function rather than a module constant so a test can monkeypatch the
    environment without reimporting the module.
    """
    from shared.config import LOCAL, get_settings

    return get_settings().environment == LOCAL
