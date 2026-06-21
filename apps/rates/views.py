from django.db.models import Q
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from apps.accounts.models import Membership
from apps.common.permissions import HasRole, IsTenantMember
from apps.rates.calculations import (
    build_rule_catalog_response,
    calculate_bill_rate_for_structure,
    serialize_decimal,
    evaluate_rule,
)
from apps.rates.models import RateCard, RateRule, RateStructure, RateStructureComponent
from apps.rates.serializers import (
    RateCardListSerializer,
    RateCardSerializer,
    RateRuleListSerializer,
    RateRulePreviewSerializer,
    RateRuleSerializer,
    RateStructureCloneSerializer,
    RateStructureListSerializer,
    RateStructurePreviewSerializer,
    RateStructureSerializer,
)
from apps.rates.services import RateCardService, RateRuleService, RateStructureService


class BaseRateViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = [Membership.ROLE_ADMIN, Membership.ROLE_MANAGER]


class RateStructureViewSet(BaseRateViewSet):
    queryset = RateStructure.objects.prefetch_related("components")
    serializer_class = RateStructureSerializer

    def get_queryset(self):
        queryset = RateStructure.objects.prefetch_related("components")

        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(name__icontains=search_term) | Q(description__icontains=search_term)
            )

        status_param = (self.request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        is_default_param = (self.request.GET.get("is_default") or "").strip().lower()
        if is_default_param in {"1", "true", "yes"}:
            queryset = queryset.filter(is_default=True)
        elif is_default_param in {"0", "false", "no"}:
            queryset = queryset.filter(is_default=False)

        return queryset.order_by("name", "id")

    def get_serializer_class(self):
        if self.action == "list":
            return RateStructureListSerializer
        return RateStructureSerializer

    def perform_destroy(self, instance):
        RateStructureService.delete_structure(
            tenant=self.request.tenant,
            user=self.request.user,
            structure=instance,
        )

    @action(detail=True, methods=["post"], url_path="clone")
    def clone(self, request, pk=None):
        serializer = RateStructureCloneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        structure = self.get_object()
        cloned = RateStructureService.clone_structure(
            tenant=request.tenant,
            user=request.user,
            structure=structure,
            name=serializer.validated_data.get("name"),
        )
        return Response(self.get_serializer(cloned).data)

    @action(detail=True, methods=["post"], url_path="preview")
    def preview(self, request, pk=None):
        serializer = RateStructurePreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        structure = self.get_object()
        raw_component_values = serializer.validated_data["component_values"]

        if isinstance(raw_component_values, list):
            component_ids = []
            for item in raw_component_values:
                try:
                    component_ids.append(int(item["rate_structure_component"]))
                except (TypeError, ValueError) as exc:
                    raise ValidationError(
                        {"component_values": "rate_structure_component must be an integer id."}
                    ) from exc

            if len(component_ids) != len(set(component_ids)):
                raise ValidationError({"component_values": "Duplicate component ids are not allowed."})

            component_map = {
                component.id: component.code
                for component in structure.components.filter(id__in=component_ids)
            }
            missing_component_ids = [component_id for component_id in component_ids if component_id not in component_map]
            if missing_component_ids:
                raise ValidationError(
                    {
                        "component_values": (
                            "All components must belong to this rate structure. "
                            f"Invalid ids: {missing_component_ids}"
                        )
                    }
                )

            component_values = {}
            for item in raw_component_values:
                component_id = int(item["rate_structure_component"])
                component_code = component_map[component_id]
                component_values[component_code] = item.get("numeric_value", item.get("value"))
        else:
            component_values = raw_component_values

        try:
            calculation = calculate_bill_rate_for_structure(
                rate_structure=structure,
                component_values=component_values,
                strict=True,
            )
        except ValueError as exc:
            raise ValidationError({"component_values": str(exc)}) from exc
        return Response(
            {
                "bill_rate": serialize_decimal(calculation["bill_rate"]),
                "base_amount": serialize_decimal(calculation["base_amount"]),
                "total_percent": serialize_decimal(calculation["total_percent"]),
                "total_fixed_amount": serialize_decimal(calculation["total_fixed_amount"]),
                "breakdown": calculation["breakdown"],
            }
        )


class RateCardViewSet(BaseRateViewSet):
    queryset = RateCard.objects.none()
    serializer_class = RateCardSerializer

    def get_queryset(self):
        queryset = RateCard.objects.select_related("role_definition", "rate_structure").prefetch_related(
            "rate_structure__components",
            "lines__supplier",
            "lines__component_values__rate_structure_component",
        )

        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(name__icontains=search_term)
                | Q(role_definition__name__icontains=search_term)
                | Q(notes__icontains=search_term)
            )

        status_param = (self.request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        role_definition_param = (
            self.request.GET.get("role_definition")
            or self.request.GET.get("role_definition_id")
            or ""
        ).strip()
        if role_definition_param:
            queryset = queryset.filter(role_definition_id=role_definition_param)

        rate_structure_param = (
            self.request.GET.get("rate_structure")
            or self.request.GET.get("rate_structure_id")
            or ""
        ).strip()
        if rate_structure_param:
            queryset = queryset.filter(rate_structure_id=rate_structure_param)

        currency_param = (self.request.GET.get("currency") or "").strip().upper()
        if currency_param:
            queryset = queryset.filter(currency=currency_param)

        unit_param = (self.request.GET.get("unit") or "").strip().lower()
        if unit_param:
            queryset = queryset.filter(unit=unit_param)

        return queryset.order_by("-effective_date", "name", "id")

    def get_serializer_class(self):
        if self.action == "list":
            return RateCardListSerializer
        return RateCardSerializer

    def perform_destroy(self, instance):
        RateCardService.delete_card(
            tenant=self.request.tenant,
            user=self.request.user,
            card=instance,
        )

    @action(detail=True, methods=["post"], url_path="recalculate")
    def recalculate(self, request, pk=None):
        card = self.get_object()
        recalculated = RateCardService.recalculate_card(
            tenant=request.tenant,
            user=request.user,
            card=card,
            strict=True,
        )
        serializer = self.get_serializer(self.get_queryset().get(pk=recalculated.pk))
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        card = self.get_object()
        activated = RateCardService.activate_card(
            tenant=request.tenant,
            user=request.user,
            card=card,
        )
        serializer = self.get_serializer(self.get_queryset().get(pk=activated.pk))
        return Response(serializer.data)


class RateRuleViewSet(BaseRateViewSet):
    queryset = RateRule.objects.select_related("rate_structure", "role_definition").prefetch_related("conditions")
    serializer_class = RateRuleSerializer

    def get_queryset(self):
        queryset = RateRule.objects.select_related("rate_structure", "role_definition").prefetch_related("conditions")

        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(name__icontains=search_term)
                | Q(description__icontains=search_term)
                | Q(role_definition__name__icontains=search_term)
                | Q(rate_structure__name__icontains=search_term)
            )

        status_param = (self.request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        role_definition_param = (
            self.request.GET.get("role_definition")
            or self.request.GET.get("role_definition_id")
            or ""
        ).strip()
        if role_definition_param:
            queryset = queryset.filter(role_definition_id=role_definition_param)

        rate_structure_param = (
            self.request.GET.get("rate_structure")
            or self.request.GET.get("rate_structure_id")
            or ""
        ).strip()
        if rate_structure_param:
            queryset = queryset.filter(rate_structure_id=rate_structure_param)

        return queryset.order_by("priority", "name", "id")

    def get_serializer_class(self):
        if self.action == "list":
            return RateRuleListSerializer
        return RateRuleSerializer

    def perform_destroy(self, instance):
        RateRuleService.delete_rule(
            tenant=self.request.tenant,
            user=self.request.user,
            rule=instance,
        )

    @action(detail=True, methods=["post"], url_path="preview")
    def preview(self, request, pk=None):
        serializer = RateRulePreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rule = self.get_object()
        evaluation = evaluate_rule(
            rule,
            context=serializer.validated_data["context"],
            base_bill_rate=serializer.validated_data["base_bill_rate"],
        )
        return Response(evaluation)


class RatesLookupsView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = [Membership.ROLE_ADMIN, Membership.ROLE_MANAGER]

    def get(self, request):
        return Response(
            {
                "rate_structure_statuses": [
                    {"value": value, "label": label} for value, label in RateStructure.STATUS_CHOICES
                ],
                "rate_structure_currency_modes": [
                    {"value": value, "label": label} for value, label in RateStructure.CURRENCY_MODE_CHOICES
                ],
                "rate_component_value_types": [
                    {"value": value, "label": label} for value, label in RateStructureComponent.VALUE_TYPE_CHOICES
                ],
                "rate_component_calculation_roles": [
                    {"value": value, "label": label}
                    for value, label in RateStructureComponent.CALCULATION_ROLE_CHOICES
                ],
                "rate_card_statuses": [{"value": value, "label": label} for value, label in RateCard.STATUS_CHOICES],
                "rate_card_units": [{"value": value, "label": label} for value, label in RateCard.UNIT_CHOICES],
                "rate_rule_statuses": [{"value": value, "label": label} for value, label in RateRule.STATUS_CHOICES],
                "rate_rule_actions": [{"value": value, "label": label} for value, label in RateRule.ACTION_CHOICES],
                "rate_rule_catalog": build_rule_catalog_response(),
            }
        )
