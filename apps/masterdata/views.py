import csv
import json
import logging
from datetime import timedelta
from html import escape
from io import StringIO
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import EmptyPage, Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.viewsets import GenericViewSet, ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import Membership, SupplierContactAccess
from apps.accounts.supplier_contact_access import (
    SupplierContactAccessError,
    contact_access_payload,
    create_contact_invitation,
    resend_contact_invitation,
    revoke_contact_access,
)
from apps.common.permissions import HasRole, IsTenantMember
from apps.masterdata.models import (
    BusinessUnit,
    Company,
    CostCenter,
    CustomField,
    JobTemplate,
    LegalEntity,
    Location,
    RateCard,
    RoleDefinition,
    Site,
    Supplier,
    SupplierCoverage,
)
from apps.masterdata.serializers import (
    BusinessUnitSerializer,
    CompanySerializer,
    CostCenterSerializer,
    CustomFieldSerializer,
    JobTemplateSerializer,
    JobTemplateUploadItemSerializer,
    LegalEntitySerializer,
    LocationSerializer,
    RateCardSerializer,
    RoleDefinitionSerializer,
    SiteSerializer,
    SupplierCoverageCreateSerializer,
    SupplierCoverageSerializer,
    SupplierCoverageUpdateSerializer,
    SupplierDetailSerializer,
    SupplierInviteCreateSerializer,
    SupplierSerializer,
    SupplierWorkerSerializer,
)
from apps.workorders.models import WorkOrder

logger = logging.getLogger(__name__)


def _positive_int_query_param(request, name, *, default):
    raw_value = (request.GET.get(name) or "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({name: "Must be a positive integer."}) from exc
    if value < 1:
        raise ValidationError({name: "Must be a positive integer."})
    return value


class BaseMasterdataViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = [Membership.ROLE_ADMIN, Membership.ROLE_MANAGER]


class CompanyViewSet(BaseMasterdataViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer


class LegalEntityViewSet(BaseMasterdataViewSet):
    queryset = LegalEntity.objects.all()
    serializer_class = LegalEntitySerializer
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
        queryset = LegalEntity.objects.all()

        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(id__icontains=search_term)
                | Q(name__icontains=search_term)
                | Q(tax_id__icontains=search_term)
                | Q(erp_code__icontains=search_term)
            )

        status_param = (self.request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        country_param = (self.request.GET.get("country") or "").strip().upper()
        if country_param:
            queryset = queryset.filter(country=country_param)

        currency_param = (self.request.GET.get("currency") or "").strip().upper()
        if currency_param:
            queryset = queryset.filter(currency=currency_param)

        return queryset.order_by("name", "id")


class BusinessUnitViewSet(BaseMasterdataViewSet):
    queryset = BusinessUnit.objects.all()
    serializer_class = BusinessUnitSerializer
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
        queryset = BusinessUnit.objects.select_related("parent", "company")

        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(code__icontains=search_term)
                | Q(name__icontains=search_term)
                | Q(description__icontains=search_term)
            )

        status_param = (self.request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        code_param = (self.request.GET.get("code") or "").strip()
        if code_param:
            queryset = queryset.filter(code=code_param)

        company_id_param = (self.request.GET.get("company_id") or "").strip()
        if company_id_param.isdigit():
            queryset = queryset.filter(company_id=int(company_id_param))

        parent_param = (
            self.request.GET.get("parent")
            or self.request.GET.get("parent_id")
            or ""
        ).strip()
        roots_only = (self.request.GET.get("roots_only") or "").strip().lower()
        if roots_only in {"1", "true", "yes"}:
            queryset = queryset.filter(parent__isnull=True)
        elif parent_param:
            if parent_param.lower() == "null":
                queryset = queryset.filter(parent__isnull=True)
            else:
                queryset = queryset.filter(parent_id=parent_param)

        return queryset.order_by("name", "code")


class CostCenterViewSet(BaseMasterdataViewSet):
    queryset = CostCenter.objects.all()
    serializer_class = CostCenterSerializer
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
        queryset = CostCenter.objects.select_related("business_unit")

        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(code__icontains=search_term)
                | Q(name__icontains=search_term)
                | Q(description__icontains=search_term)
                | Q(owner_email__icontains=search_term)
            )

        status_param = (self.request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        code_param = (self.request.GET.get("code") or "").strip()
        if code_param:
            queryset = queryset.filter(code=code_param)

        business_unit_param = (
            self.request.GET.get("business_unit")
            or self.request.GET.get("business_unit_id")
            or ""
        ).strip()
        if business_unit_param:
            queryset = queryset.filter(business_unit_id=business_unit_param)

        currency_param = (self.request.GET.get("currency") or "").strip().upper()
        if currency_param:
            queryset = queryset.filter(currency=currency_param)

        owner_email_param = (self.request.GET.get("owner_email") or "").strip()
        if owner_email_param:
            queryset = queryset.filter(owner_email__iexact=owner_email_param)

        return queryset.order_by("name", "code")


class LocationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    queryset = Location.objects.all()
    serializer_class = LocationSerializer
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
        queryset = Location.objects.all()

        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(name__icontains=search_term)
                | Q(country__icontains=search_term)
                | Q(region__icontains=search_term)
            )

        status_param = (self.request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        country_param = (self.request.GET.get("country") or "").strip()
        if country_param:
            queryset = queryset.filter(country__iexact=country_param)

        region_param = (self.request.GET.get("region") or "").strip()
        if region_param:
            queryset = queryset.filter(region__icontains=region_param)

        return queryset.order_by("name", "country", "region", "id")


class SiteViewSet(BaseMasterdataViewSet):
    queryset = Site.objects.all()
    serializer_class = SiteSerializer
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
        queryset = Site.objects.select_related("legal_entity")

        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(code__icontains=search_term)
                | Q(name__icontains=search_term)
                | Q(address_line1__icontains=search_term)
                | Q(city__icontains=search_term)
                | Q(state_province__icontains=search_term)
                | Q(postal_code__icontains=search_term)
            )

        status_param = (self.request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        code_param = (self.request.GET.get("code") or "").strip()
        if code_param:
            queryset = queryset.filter(code=code_param)

        country_param = (self.request.GET.get("country") or "").strip().upper()
        if country_param:
            queryset = queryset.filter(country=country_param)

        currency_param = (self.request.GET.get("currency") or "").strip().upper()
        if currency_param:
            queryset = queryset.filter(currency=currency_param)

        legal_entity_param = (
            self.request.GET.get("legal_entity")
            or self.request.GET.get("legal_entity_id")
            or ""
        ).strip()
        if legal_entity_param:
            queryset = queryset.filter(legal_entity_id=legal_entity_param)

        timezone_param = (self.request.GET.get("timezone") or "").strip()
        if timezone_param:
            queryset = queryset.filter(timezone=timezone_param)

        return queryset.order_by("name", "code")


class SupplierViewSet(BaseMasterdataViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
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

    WORKERS_DEFAULT_PAGE_SIZE = 25
    WORKERS_MAX_PAGE_SIZE = 100

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SupplierDetailSerializer
        return SupplierSerializer

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy"}:
            self.required_roles = self.manage_roles
        elif self.action in {
            "invite",
            "contact_access",
            "contact_access_resend",
            "contact_access_revoke",
        }:
            self.required_roles = [Membership.ROLE_ADMIN]
        elif self.action == "coverage" and self.request.method == "POST":
            self.required_roles = [Membership.ROLE_ADMIN]
        elif self.action == "coverage_update":
            self.required_roles = [Membership.ROLE_ADMIN]
        return super().get_permissions()

    def get_queryset(self):
        queryset = Supplier.objects.all()
        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(name__icontains=search_term)
                | Q(supplier_code__icontains=search_term)
                | Q(email__icontains=search_term)
                | Q(contact_name__icontains=search_term)
                | Q(contact_email__icontains=search_term)
                | Q(category__icontains=search_term)
                | Q(owner_name__icontains=search_term)
            )

        status_param = (self.request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        supplier_type = (self.request.GET.get("type") or "").strip().lower()
        if supplier_type:
            queryset = queryset.filter(supplier_type=supplier_type)

        return queryset.order_by("name")

    @action(detail=True, methods=["get"], url_path="workers")
    def workers(self, request, pk=None):
        supplier = self.get_object()
        tenant_id = request.tenant.id

        page = _positive_int_query_param(request, "page", default=1)
        page_size = min(
            _positive_int_query_param(
                request,
                "page_size",
                default=self.WORKERS_DEFAULT_PAGE_SIZE,
            ),
            self.WORKERS_MAX_PAGE_SIZE,
        )

        # The supplier foreign key is the assignment boundary. Tenant schemas
        # are authoritative; tenant_id is an additional guard while allowing
        # legacy tenant-schema rows created before tenant_id was populated.
        queryset = (
            WorkOrder.objects.filter(supplier_id=supplier.id)
            .filter(Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True))
            .select_related("role_definition", "site")
            .order_by("-created_at", "-id")
        )
        paginator = Paginator(queryset, page_size)
        try:
            page_obj = paginator.page(page)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else None

        records = list(page_obj.object_list) if page_obj is not None else []
        return Response(
            {
                "supplier_id": supplier.id,
                "results": SupplierWorkerSerializer(records, many=True).data,
                "pagination": {
                    "page": page_obj.number if page_obj is not None else 1,
                    "page_size": page_size,
                    "total_count": paginator.count,
                    "total_pages": paginator.num_pages,
                    "has_next": bool(page_obj and page_obj.has_next()),
                    "has_previous": bool(page_obj and page_obj.has_previous()),
                },
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get", "post"], url_path="coverage")
    def coverage(self, request, pk=None):
        supplier = self.get_object()
        if request.method == "POST":
            return self._create_or_reactivate_coverage(request, supplier)

        records = (
            SupplierCoverage.objects.filter(supplier=supplier)
            .select_related("role", "site")
            .order_by("role__name", "site__name", "id")
        )
        return Response(
            {
                "supplier_id": supplier.id,
                "results": SupplierCoverageSerializer(records, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    def _create_or_reactivate_coverage(self, request, supplier):
        payload = SupplierCoverageCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        role = payload.validated_data["role"]
        site = payload.validated_data["site"]

        with transaction.atomic():
            existing = (
                SupplierCoverage.objects.select_for_update()
                .filter(supplier=supplier, role=role, site=site)
                .first()
            )
            if existing is not None:
                if existing.is_active:
                    return Response(
                        {"detail": "This supplier role and site coverage is already active."},
                        status=status.HTTP_409_CONFLICT,
                    )
                existing.is_active = True
                existing.save(update_fields=["is_active", "updated_at"])
                return Response(
                    SupplierCoverageSerializer(existing).data,
                    status=status.HTTP_200_OK,
                )

            try:
                with transaction.atomic():
                    coverage = SupplierCoverage.objects.create(
                        supplier=supplier,
                        role=role,
                        site=site,
                    )
            except IntegrityError:
                existing = SupplierCoverage.objects.select_for_update().get(
                    supplier=supplier,
                    role=role,
                    site=site,
                )
                if existing.is_active:
                    return Response(
                        {"detail": "This supplier role and site coverage is already active."},
                        status=status.HTTP_409_CONFLICT,
                    )
                existing.is_active = True
                existing.save(update_fields=["is_active", "updated_at"])
                coverage = existing
                response_status = status.HTTP_200_OK
            else:
                response_status = status.HTTP_201_CREATED

        return Response(
            SupplierCoverageSerializer(coverage).data,
            status=response_status,
        )

    @action(
        detail=True,
        methods=["patch"],
        url_path=r"coverage/(?P<coverage_id>[^/.]+)",
        url_name="coverage-update",
    )
    def coverage_update(self, request, pk=None, coverage_id=None):
        supplier = self.get_object()
        payload = SupplierCoverageUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        coverage = SupplierCoverage.objects.filter(
            id=coverage_id,
            supplier=supplier,
        ).first()
        if coverage is None:
            return Response(
                {"detail": "Coverage record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        coverage.is_active = payload.validated_data["is_active"]
        coverage.save(update_fields=["is_active", "updated_at"])
        return Response(
            SupplierCoverageSerializer(coverage).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="invite")
    def invite(self, request, pk=None):
        supplier = self.get_object()
        serializer = SupplierInviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            access = create_contact_invitation(
                tenant=request.tenant,
                supplier_id=supplier.id,
                email=data["email"],
                invited_by=request.user,
                base_url=request.build_absolute_uri("/"),
                expires_in_days=data["expires_in_days"],
            )
        except SupplierContactAccessError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(contact_access_payload(access), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="contact-access")
    def contact_access(self, request, pk=None):
        supplier = self.get_object()
        records = SupplierContactAccess.objects.filter(
            tenant=request.tenant,
            supplier_id=supplier.id,
        ).prefetch_related("invitations")
        return Response(
            {"supplier_id": supplier.id, "results": [contact_access_payload(record) for record in records]},
            status=status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"contact-access/(?P<access_id>[^/.]+)/resend",
        url_name="contact-access-resend",
    )
    def contact_access_resend(self, request, pk=None, access_id=None):
        supplier = self.get_object()
        serializer = SupplierInviteCreateSerializer(
            data={"email": request.data.get("email", "placeholder@example.invalid"), **request.data}
        )
        serializer.is_valid(raise_exception=True)
        try:
            access = resend_contact_invitation(
                tenant=request.tenant,
                supplier_id=supplier.id,
                access_id=access_id,
                invited_by=request.user,
                base_url=request.build_absolute_uri("/"),
                expires_in_days=serializer.validated_data["expires_in_days"],
            )
        except SupplierContactAccessError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(contact_access_payload(access), status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"contact-access/(?P<access_id>[^/.]+)/revoke",
        url_name="contact-access-revoke",
    )
    def contact_access_revoke(self, request, pk=None, access_id=None):
        supplier = self.get_object()
        try:
            access = revoke_contact_access(
                tenant=request.tenant,
                supplier_id=supplier.id,
                access_id=access_id,
            )
        except SupplierContactAccessError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(contact_access_payload(access), status=status.HTTP_200_OK)


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
]

TEMPLATE_MANAGE_ROLES = [
    Membership.ROLE_ADMIN,
    Membership.ROLE_MANAGER,
    Membership.ROLE_BUSINESS,
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


class RoleDefinitionViewSet(BaseMasterdataViewSet):
    queryset = RoleDefinition.objects.all()
    serializer_class = RoleDefinitionSerializer

    def get_queryset(self):
        queryset = RoleDefinition.objects.all()

        search_term = (self.request.GET.get("search") or self.request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(code__icontains=search_term)
                | Q(name__icontains=search_term)
                | Q(description__icontains=search_term)
                | Q(country__icontains=search_term)
                | Q(region__icontains=search_term)
                | Q(city__icontains=search_term)
            )

        active_param = (self.request.GET.get("is_active") or self.request.GET.get("active") or "").strip().lower()
        if active_param in {"1", "true", "yes"}:
            queryset = queryset.filter(is_active=True)
        elif active_param in {"0", "false", "no"}:
            queryset = queryset.filter(is_active=False)

        country = (self.request.GET.get("country") or "").strip().upper()
        if country:
            queryset = queryset.filter(country=country)

        region = (self.request.GET.get("region") or "").strip()
        if region:
            queryset = queryset.filter(region__icontains=region)

        city = (self.request.GET.get("city") or "").strip()
        if city:
            queryset = queryset.filter(city__icontains=city)

        default_currency = (self.request.GET.get("default_currency") or "").strip().upper()
        if default_currency:
            queryset = queryset.filter(default_currency=default_currency)

        default_unit = (self.request.GET.get("default_unit") or "").strip().lower()
        if default_unit:
            queryset = queryset.filter(default_unit=default_unit)

        return queryset.order_by("name", "country", "region", "city", "id")


def _build_supplier_invite_email_text(tenant_name, registration_link, expires_at):
    expires_text = timezone.localtime(expires_at).strftime("%Y-%m-%d %H:%M %Z")
    return (
        f"You've been invited to join {tenant_name} on LEVV as a supplier.\n\n"
        f"Complete your registration using this link:\n{registration_link}\n\n"
        f"This invite expires on {expires_text}.\n\n"
        "If you were not expecting this email, you can safely ignore it."
    )


def _build_supplier_invite_email_html(tenant_name, registration_link, expires_at):
    expires_text = timezone.localtime(expires_at).strftime("%Y-%m-%d %H:%M %Z")
    tenant_name_safe = escape(tenant_name)
    link_safe = escape(registration_link, quote=True)
    expires_text_safe = escape(expires_text)

    return f"""
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f4f7fb;font-family:Arial,sans-serif;color:#0f172a;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:32px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:620px;background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;">
            <tr>
              <td style="background:#0b1f4d;padding:18px 24px;color:#ffffff;font-size:18px;font-weight:700;">
                LEVV Supplier Invite
              </td>
            </tr>
            <tr>
              <td style="padding:24px;">
                <p style="margin:0 0 12px;font-size:16px;line-height:1.5;">
                  You've been invited to join <strong>{tenant_name_safe}</strong> on LEVV as a supplier.
                </p>
                <p style="margin:0 0 20px;font-size:15px;line-height:1.5;color:#334155;">
                  Click the button below to complete your registration.
                </p>
                <p style="margin:0 0 20px;">
                  <a href="{link_safe}" style="display:inline-block;background:#0b1f4d;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:8px;font-weight:600;">
                    Complete Registration
                  </a>
                </p>
                <p style="margin:0 0 10px;font-size:13px;color:#475569;">
                  If the button does not work, copy and paste this URL into your browser:
                </p>
                <p style="margin:0 0 20px;font-size:13px;line-height:1.5;word-break:break-all;">
                  <a href="{link_safe}" style="color:#1d4ed8;">{link_safe}</a>
                </p>
                <p style="margin:0 0 8px;font-size:13px;color:#475569;">
                  This invite expires on <strong>{expires_text_safe}</strong>.
                </p>
                <p style="margin:0;font-size:12px;color:#64748b;">
                  If you were not expecting this email, you can safely ignore it.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()
