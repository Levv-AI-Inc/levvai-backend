from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from apps.accounts.models import Membership
from apps.common.permissions import HasRole, IsTenantMember
from apps.masterdata.models import (
    BusinessUnit,
    Company,
    CostCenter,
    CustomField,
    RateCard,
    Site,
    Supplier,
)
from apps.masterdata.serializers import (
    BusinessUnitSerializer,
    CompanySerializer,
    CostCenterSerializer,
    CustomFieldSerializer,
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
