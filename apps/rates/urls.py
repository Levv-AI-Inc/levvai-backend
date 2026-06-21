from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.rates.views import RateCardViewSet, RateRuleViewSet, RateStructureViewSet, RatesLookupsView

router = DefaultRouter()
router.register(r"rate-structures", RateStructureViewSet, basename="ratestructure")
router.register(r"rate-cards", RateCardViewSet, basename="ratecard")
router.register(r"rate-rules", RateRuleViewSet, basename="raterule")

urlpatterns = [
    path("rates/lookups/", RatesLookupsView.as_view(), name="rates-lookups"),
]
urlpatterns += router.urls
