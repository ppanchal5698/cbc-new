from rest_framework.routers import DefaultRouter

from .views import (
    DocElementViewSet,
    ExtractionRunViewSet,
    FieldProvenanceViewSet,
    MatchViewSet,
    OpeningViewSet,
)

router = DefaultRouter()
router.register(r"openings", OpeningViewSet, basename="opening")
router.register(r"doc-elements", DocElementViewSet, basename="doc-element")
router.register(r"extraction-runs", ExtractionRunViewSet, basename="extraction-run")
router.register(r"provenance", FieldProvenanceViewSet, basename="provenance")
router.register(r"matches", MatchViewSet, basename="match")

urlpatterns = router.urls
