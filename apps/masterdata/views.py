import csv
import json
from io import StringIO

from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import Membership
from apps.common.permissions import HasRole, IsTenantMember
from apps.masterdata.models import (
    BusinessUnit,
    Company,
    CostCenter,
    CustomField,
    JobTemplate,
    RateCard,
    Site,
    Supplier,
)
from apps.masterdata.serializers import (
    BusinessUnitSerializer,
    CompanySerializer,
    CostCenterSerializer,
    CustomFieldSerializer,
    JobTemplateSerializer,
    JobTemplateUploadItemSerializer,
    RateCardSerializer,
    SiteSerializer,
    SupplierSerializer,
)


class BaseMasterdataViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = [Membership.ROLE_ADMIN, Membership.ROLE_MANAGER]


class CompanyViewSet(BaseMasterdataViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer


class BusinessUnitViewSet(BaseMasterdataViewSet):
    queryset = BusinessUnit.objects.all()
    serializer_class = BusinessUnitSerializer


class CostCenterViewSet(BaseMasterdataViewSet):
    queryset = CostCenter.objects.all()
    serializer_class = CostCenterSerializer


class SiteViewSet(BaseMasterdataViewSet):
    queryset = Site.objects.all()
    serializer_class = SiteSerializer


class SupplierViewSet(BaseMasterdataViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer


class RateCardViewSet(BaseMasterdataViewSet):
    queryset = RateCard.objects.all()
    serializer_class = RateCardSerializer


class CustomFieldViewSet(BaseMasterdataViewSet):
    queryset = CustomField.objects.all()
    serializer_class = CustomFieldSerializer


TEMPLATE_VIEW_ROLES = [
    Membership.ROLE_ADMIN,
    Membership.ROLE_MANAGER,
    Membership.ROLE_BUSINESS,
    Membership.ROLE_HIRING_MANAGER,
    Membership.ROLE_PROCUREMENT_MANAGER,
]

TEMPLATE_MANAGE_ROLES = [
    Membership.ROLE_ADMIN,
    Membership.ROLE_MANAGER,
    Membership.ROLE_BUSINESS,
    Membership.ROLE_HIRING_MANAGER,
    Membership.ROLE_PROCUREMENT_MANAGER,
]


class JobTemplateViewSet(BaseMasterdataViewSet):
    queryset = JobTemplate.objects.all()
    serializer_class = JobTemplateSerializer
    required_roles = TEMPLATE_VIEW_ROLES

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy", "upload"}:
            self.required_roles = TEMPLATE_MANAGE_ROLES
        else:
            self.required_roles = TEMPLATE_VIEW_ROLES
        return super().get_permissions()

    def get_queryset(self):
        queryset = JobTemplate.objects.all()
        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(role__icontains=search_term)
                | Q(description__icontains=search_term)
                | Q(country__icontains=search_term)
                | Q(region_in_country__icontains=search_term)
            )

        country = (self.request.GET.get("country") or "").strip()
        if country:
            queryset = queryset.filter(country__iexact=country.upper())

        region = (self.request.GET.get("region") or "").strip()
        if region:
            queryset = queryset.filter(region_in_country__icontains=region)

        return queryset.order_by("role", "country", "region_in_country")

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        rows, parse_error = self._load_upload_rows(request)
        if parse_error:
            return Response({"detail": parse_error}, status=status.HTTP_400_BAD_REQUEST)
        if not rows:
            return Response({"detail": "No template rows were provided."}, status=status.HTTP_400_BAD_REQUEST)

        created_count = 0
        updated_count = 0
        errors = []

        for index, row in enumerate(rows, start=1):
            serializer = JobTemplateUploadItemSerializer(data=row)
            if not serializer.is_valid():
                errors.append({"row": index, "errors": serializer.errors})
                continue

            validated = serializer.validated_data
            _, created = JobTemplate.objects.update_or_create(
                role=validated["role"],
                country=validated["country"],
                region_in_country=validated.get("region_in_country", ""),
                defaults={"description": validated.get("description", "")},
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        response_status = status.HTTP_200_OK
        if errors and (created_count or updated_count):
            response_status = status.HTTP_207_MULTI_STATUS
        elif errors:
            response_status = status.HTTP_400_BAD_REQUEST

        return Response(
            {
                "created": created_count,
                "updated": updated_count,
                "failed": len(errors),
                "errors": errors,
            },
            status=response_status,
        )

    def _load_upload_rows(self, request):
        upload_file = request.FILES.get("file")
        if upload_file is not None:
            return self._load_csv_rows(upload_file)

        payload = request.data
        if isinstance(payload, list):
            return payload, None
        if not isinstance(payload, dict):
            return None, "Upload body must be either a JSON array or an object with a templates array."

        templates = payload.get("templates")
        if isinstance(templates, str):
            try:
                templates = json.loads(templates)
            except json.JSONDecodeError:
                return None, "templates must be valid JSON."

        if not isinstance(templates, list):
            return None, "templates must be a JSON array."
        return templates, None

    def _load_csv_rows(self, upload_file):
        try:
            content = upload_file.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return None, "CSV file must be UTF-8 encoded."
        except Exception:
            return None, "Unable to read uploaded file."

        try:
            reader = csv.DictReader(StringIO(content))
        except Exception:
            return None, "Unable to parse CSV file."

        if not reader.fieldnames:
            return None, "CSV file is missing a header row."
        return list(reader), None
