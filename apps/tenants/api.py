from django.core.management import call_command
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.models import Domain, Tenant
from apps.tenants.provisioning import enqueue_domain_provision


class TenantCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    schema_name = serializers.SlugField(max_length=63)
    domain = serializers.CharField(max_length=255)


class TenantCreateView(APIView):
    """Public admin endpoint to create a tenant + schema + domain mapping."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = TenantCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        tenant = Tenant.objects.create(
            name=data["name"],
            schema_name=data["schema_name"],
        )

        Domain.objects.create(domain=data["domain"], tenant=tenant, is_primary=True)
        enqueue_domain_provision(data["domain"], schema_name=tenant.schema_name)

        # Run tenant migrations for the new schema.
        call_command("migrate_schemas", schema_name=tenant.schema_name, interactive=False, verbosity=0)

        return Response({"id": tenant.id, "schema_name": tenant.schema_name}, status=status.HTTP_201_CREATED)
