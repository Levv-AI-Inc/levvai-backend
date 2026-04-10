from rest_framework.routers import DefaultRouter

from apps.approvals.views import ApprovalChainViewSet

router = DefaultRouter()
router.register(r"approval-chains", ApprovalChainViewSet, basename="approvalchain")

urlpatterns = router.urls

