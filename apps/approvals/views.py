from django.db.models import Q
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.accounts.models import Membership
from apps.approvals.catalog import build_catalog_response
from apps.approvals.engine import evaluate_chain
from apps.approvals.models import ApprovalChain
from apps.approvals.serializers import ApprovalChainSerializer, ApprovalChainSimulationSerializer
from apps.approvals.services import ApprovalChainService
from apps.common.permissions import HasRole, IsTenantMember


class ApprovalChainViewSet(ModelViewSet):
    queryset = ApprovalChain.objects.all()
    serializer_class = ApprovalChainSerializer
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = [
        Membership.ROLE_ADMIN,
        Membership.ROLE_MANAGER,
        Membership.ROLE_BUSINESS,
        Membership.ROLE_FINANCE,
        Membership.ROLE_VIEWER,
    ]
    manage_roles = [
        Membership.ROLE_ADMIN,
        Membership.ROLE_MANAGER,
    ]

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy"}:
            self.required_roles = self.manage_roles
        return super().get_permissions()

    def get_queryset(self):
        queryset = ApprovalChain.objects.prefetch_related("conditions", "steps__approver")

        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(name__icontains=search_term) | Q(description__icontains=search_term)
            )

        active_param = (self.request.GET.get("is_active") or self.request.GET.get("active") or "").strip().lower()
        if active_param in {"1", "true", "yes"}:
            queryset = queryset.filter(is_active=True)
        elif active_param in {"0", "false", "no"}:
            queryset = queryset.filter(is_active=False)

        match_strategy_param = (self.request.GET.get("match_strategy") or "").strip().lower()
        if match_strategy_param:
            queryset = queryset.filter(match_strategy=match_strategy_param)

        return queryset.order_by("priority", "name", "id")

    def perform_destroy(self, instance):
        ApprovalChainService.delete_chain(
            tenant=self.request.tenant,
            user=self.request.user,
            chain=instance,
        )

    @action(detail=False, methods=["get"], url_path="catalog")
    def catalog(self, request):
        return Response(build_catalog_response())

    @action(detail=False, methods=["post"], url_path="simulate")
    def simulate(self, request):
        serializer = ApprovalChainSimulationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        queryset = self.get_queryset()
        if not serializer.validated_data["include_inactive"]:
            queryset = queryset.filter(is_active=True)

        payload = serializer.validated_data["payload"]
        include_non_matches = serializer.validated_data["include_non_matches"]

        results = []
        for chain in queryset:
            evaluation = evaluate_chain(chain, payload)
            if evaluation["matched"] or include_non_matches:
                results.append(
                    {
                        "chain": self.get_serializer(chain).data,
                        "evaluation": evaluation,
                    }
                )

        return Response(
            {
                "results": results,
                "meta": {
                    "evaluated_count": queryset.count(),
                    "match_count": sum(1 for item in results if item["evaluation"]["matched"]),
                },
            }
        )

