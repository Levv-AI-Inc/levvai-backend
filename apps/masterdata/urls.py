from rest_framework.routers import DefaultRouter

from apps.masterdata.views import (
    BusinessUnitViewSet,
    CompanyViewSet,
    CostCenterViewSet,
    CustomFieldViewSet,
    JobTemplateViewSet,
    LegalEntityViewSet,
    LocationViewSet,
    RoleDefinitionViewSet,
    SiteViewSet,
    SupplierViewSet,
)

router = DefaultRouter()
router.register(r"companies", CompanyViewSet, basename="company")
router.register(r"legal-entities", LegalEntityViewSet, basename="legalentity")
router.register(r"business-units", BusinessUnitViewSet, basename="businessunit")
router.register(r"cost-centers", CostCenterViewSet, basename="costcenter")
router.register(r"locations", LocationViewSet, basename="location")
router.register(r"sites", SiteViewSet, basename="site")
router.register(r"suppliers", SupplierViewSet, basename="supplier")
router.register(r"custom-fields", CustomFieldViewSet, basename="customfield")
router.register(r"job-templates", JobTemplateViewSet, basename="jobtemplate")
router.register(r"roles", RoleDefinitionViewSet, basename="roledefinition")

urlpatterns = router.urls
