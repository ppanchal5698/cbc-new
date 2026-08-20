"""Operational endpoints (§11.5)."""

from django.db import connection
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
