from rest_framework.routers import DefaultRouter

from .views import ExtractionMetricViewSet, FeedbackViewSet

router = DefaultRouter()
router.register(r"feedback", FeedbackViewSet, basename="feedback")
router.register(r"extraction-metrics", ExtractionMetricViewSet, basename="extraction-metric")

urlpatterns = router.urls
