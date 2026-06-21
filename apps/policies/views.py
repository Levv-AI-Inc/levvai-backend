from django.db.models import Q
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.accounts.models import Membership
from apps.common.permissions import HasRole, IsTenantMember
from apps.policies.models import (
    WorkerLifecycleWorkflow,
    WorkflowBlock,
    WorkflowPolicyScope,
    WorkflowPolicyScopeField,
    WorkflowRequirement,
)
from apps.policies.serializers import WorkerLifecycleWorkflowSerializer


class WorkerLifecycleWorkflowViewSet(ModelViewSet):
    serializer_class = WorkerLifecycleWorkflowSerializer
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = [Membership.ROLE_ADMIN, Membership.ROLE_MANAGER]

    def get_queryset(self):
        queryset = (
            WorkerLifecycleWorkflow.objects.select_related("created_by", "policy_scope")
            .prefetch_related(
                "policy_scope__fields__location",
                "policy_scope__fields__cost_center",
                "policy_scope__fields__business_unit",
                "policy_scope__fields__role_definition",
                "policy_scope__fields__supplier",
                "blocks__requirements__requirement",
            )
            .all()
        )

        tenant = getattr(self.request, "tenant", None)
        if tenant and tenant.schema_name != "public":
            queryset = queryset.filter(Q(tenant_id=tenant.id) | Q(tenant_id__isnull=True))

        workflow_type = (
            self.request.GET.get("workflow_type")
            or self.request.GET.get("type")
            or ""
        ).strip().lower()
        if workflow_type:
            queryset = queryset.filter(workflow_type=workflow_type)

        status_param = (self.request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        active_param = (self.request.GET.get("is_active") or self.request.GET.get("active") or "").strip().lower()
        if active_param in {"1", "true", "yes"}:
            queryset = queryset.filter(is_active=True)
        elif active_param in {"0", "false", "no"}:
            queryset = queryset.filter(is_active=False)

        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(name__icontains=search_term)

        return queryset.order_by("workflow_type", "name", "id")

    @action(detail=False, methods=["get"], url_path="lookups")
    def lookups(self, request):
        return Response(
            {
                "workflow_types": [
                    {"value": value, "label": label} for value, label in WorkerLifecycleWorkflow.TYPE_CHOICES
                ],
                "workflow_statuses": [
                    {"value": value, "label": label} for value, label in WorkerLifecycleWorkflow.STATUS_CHOICES
                ],
                "worker_types": [
                    {"value": value, "label": label} for value, label in WorkflowPolicyScope.WORKER_TYPE_CHOICES
                ],
                "scope_fields": [
                    {"value": value, "label": label} for value, label in WorkflowPolicyScopeField.FIELD_CHOICES
                ],
                "scope_operators": [
                    {"value": value, "label": label} for value, label in WorkflowPolicyScopeField.OPERATOR_CHOICES
                ],
                "block_types": [
                    {"value": value, "label": label} for value, label in WorkflowBlock.TYPE_CHOICES
                ],
                "gate_types": [
                    {"value": value, "label": label} for value, label in WorkflowBlock.GATE_CHOICES
                ],
                "integration_types": [
                    {"value": value, "label": label} for value, label in WorkflowBlock.INTEGRATION_CHOICES
                ],
                "requirement_owners": [
                    {"value": value, "label": label} for value, label in WorkflowRequirement.OWNER_CHOICES
                ],
            }
        )
