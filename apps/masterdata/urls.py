from rest_framework.routers import DefaultRouter

from apps.masterdata.views import (
    BusinessUnitViewSet,
    CompanyViewSet,
    CostCenterViewSet,
    CustomFieldViewSet,
    JobTemplateViewSet,
    RateCardViewSet,
    SiteViewSet,
    SupplierViewSet,
)

router = DefaultRouter()
router.register(r"companies", CompanyViewSet, basename="company")
router.register(r"business-units", BusinessUnitViewSet, basename="businessunit")
router.register(r"cost-centers", CostCenterViewSet, basename="costcenter")
router.register(r"sites", SiteViewSet, basename="site")
router.register(r"suppliers", SupplierViewSet, basename="supplier")
router.register(r"rate-cards", RateCardViewSet, basename="ratecard")
router.register(r"custom-fields", CustomFieldViewSet, basename="customfield")
router.register(r"job-templates", JobTemplateViewSet, basename="jobtemplate")

urlpatterns = router.urls
