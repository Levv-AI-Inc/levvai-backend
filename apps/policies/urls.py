from rest_framework.routers import DefaultRouter

from apps.policies.views import WorkerLifecycleWorkflowViewSet

router = DefaultRouter()
router.register(r"compliance/workflows", WorkerLifecycleWorkflowViewSet, basename="compliance-workflow")
router.register(r"compliance/policies", WorkerLifecycleWorkflowViewSet, basename="compliance-policy")

urlpatterns = router.urls
