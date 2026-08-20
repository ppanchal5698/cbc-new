from rest_framework.routers import DefaultRouter

from .views import QuoteLineViewSet, QuoteViewSet, VendorRFQViewSet

router = DefaultRouter()
router.register(r"quotes", QuoteViewSet, basename="quote")
router.register(r"quote-lines", QuoteLineViewSet, basename="quote-line")
router.register(r"vendor-rfqs", VendorRFQViewSet, basename="vendor-rfq")

urlpatterns = router.urls
