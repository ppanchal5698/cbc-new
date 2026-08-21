"""
Root URL configuration.

Routes are flat: ``/api/projects/`` not ``/api/projects/projects/``. The previous
layout mounted a router that registered ``projects`` under a prefix that was
already ``projects``, producing a doubled segment in every URL.
"""

from common.views import ScalarDocsView
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("authentication.urls")),
    path("api/", include("common.urls")),
    path("api/", include("projects.urls")),
    path("api/", include("openings.urls")),
    path("api/", include("catalog.urls")),
    path("api/", include("pricing.urls")),
    path("api/", include("quotes.urls")),
    path("api/", include("feedback.urls")),
    # The frontend generates its types from this schema; it is never hand-typed
    # (§8.2, fixes defect H2).
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    # Scalar: the schema as a browsable, callable reference. Sits alongside
    # Swagger UI rather than replacing it — they read the same document, and
    # people have preferences.
    path("api/docs/", ScalarDocsView.as_view(), name="scalar-docs"),
    path("api/schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/schema/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
