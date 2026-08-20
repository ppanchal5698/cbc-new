from rest_framework.routers import DefaultRouter

from .views import (
    BidAlternateViewSet,
    DocumentManifestViewSet,
    DocumentViewSet,
    PipelineJobViewSet,
    ProjectViewSet,
)

router = DefaultRouter()
router.register(r"projects", ProjectViewSet, basename="project")
router.register(r"documents", DocumentViewSet, basename="document")
router.register(r"manifest", DocumentManifestViewSet, basename="manifest")
router.register(r"pipeline-jobs", PipelineJobViewSet, basename="pipeline-job")
router.register(r"bid-alternates", BidAlternateViewSet, basename="bid-alternate")

urlpatterns = router.urls
