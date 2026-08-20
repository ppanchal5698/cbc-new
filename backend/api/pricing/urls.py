from rest_framework.routers import DefaultRouter

from .views import (
    FinishCodeViewSet,
    MarginBandViewSet,
    TaxRateViewSet,
    ThroatDepthViewSet,
    VendorMultiplierViewSet,
)

router = DefaultRouter()
router.register(r"finish-codes", FinishCodeViewSet, basename="finish-code")
router.register(r"throat-depths", ThroatDepthViewSet, basename="throat-depth")
router.register(r"margin-bands", MarginBandViewSet, basename="margin-band")
router.register(r"vendor-multipliers", VendorMultiplierViewSet, basename="vendor-multiplier")
router.register(r"tax-rates", TaxRateViewSet, basename="tax-rate")

urlpatterns = router.urls
