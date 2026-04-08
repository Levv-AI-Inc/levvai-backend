import csv
import json
import logging
from datetime import timedelta
from html import escape
from io import StringIO
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import Membership, SupplierInvite
from apps.common.permissions import HasRole, IsTenantMember
from apps.masterdata.models import (
    BusinessUnit,
    Company,
    CostCenter,
    CustomField,
    JobTemplate,
    LegalEntity,
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
    LegalEntitySerializer,
    RateCardSerializer,
    SiteSerializer,
    SupplierInviteCreateSerializer,
    SupplierSerializer,
)

logger = logging.getLogger(__name__)


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

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy", "invite"}:
            self.required_roles = self.manage_roles
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

    @action(detail=True, methods=["post"], url_path="invite")
    def invite(self, request, pk=None):
        if not hasattr(request, "tenant") or request.tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Keep action role checks strict even if permissions are customized later.
        membership = Membership.objects.filter(
            user=request.user,
            tenant_id=request.tenant.id,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        ).first()
        if not membership or membership.role not in set(self.manage_roles):
            raise PermissionDenied()

        supplier = self.get_object()
        serializer = SupplierInviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        email = data["email"].strip().lower()
        expires_at = timezone.now() + timedelta(days=data["expires_in_days"])
        base_url = request.build_absolute_uri("/").rstrip("/")
        invite = None

        try:
            with transaction.atomic():
                invite = SupplierInvite.objects.create(
                    tenant=request.tenant,
                    supplier_id=supplier.id,
                    email=email,
                    invited_by=request.user,
                    expires_at=expires_at,
                )
                if supplier.status != Supplier.STATUS_INVITED:
                    supplier.status = Supplier.STATUS_INVITED
                    supplier.save(update_fields=["status"])

                query = urlencode({"mode": "register", "invite_token": invite.token, "email": email})
                registration_link = f"{base_url}/auth/login?{query}"
                subject = f"You're invited to join {request.tenant.name} on LEVV"
                text_body = _build_supplier_invite_email_text(
                    tenant_name=request.tenant.name,
                    registration_link=registration_link,
                    expires_at=invite.expires_at,
                )
                html_body = _build_supplier_invite_email_html(
                    tenant_name=request.tenant.name,
                    registration_link=registration_link,
                    expires_at=invite.expires_at,
                )

                msg = EmailMultiAlternatives(
                    subject=subject,
                    body=text_body,
                    from_email=settings.SUPPLIER_INVITE_FROM_EMAIL,
                    to=[email],
                )
                msg.attach_alternative(html_body, "text/html")
                msg.send(fail_silently=False)
        except Exception:
            logger.exception(
                "supplier_invite_email_failed tenant_id=%s supplier_id=%s email=%s",
                request.tenant.id,
                supplier.id,
                email,
            )
            return Response(
                {"detail": "Invite email could not be sent. Verify email provider settings and retry."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {
                "invite_id": invite.id,
                "supplier_id": supplier.id,
                "email": invite.email,
                "token": invite.token,
                "expires_at": invite.expires_at,
                "registration_link": registration_link,
            },
            status=status.HTTP_201_CREATED,
        )


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
