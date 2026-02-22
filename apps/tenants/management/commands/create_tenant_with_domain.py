from django.core.management.base import BaseCommand

from apps.tenants.models import Domain, Tenant


class Command(BaseCommand):
    help = "Create a tenant and associate a domain."

    def add_arguments(self, parser):
        parser.add_argument("--schema", required=True, help="Tenant schema name (e.g., test)")
        parser.add_argument("--name", required=True, help="Tenant display name")
        parser.add_argument("--domain", required=True, help="Domain to map to tenant")
        parser.add_argument(
            "--primary",
            action="store_true",
            help="Mark domain as primary for this tenant",
        )

    def handle(self, *args, **options):
        schema_name = options["schema"].strip()
        name = options["name"].strip()
        domain = options["domain"].strip().lower()
        is_primary = bool(options["primary"])

        tenant, created = Tenant.objects.get_or_create(
            schema_name=schema_name,
            defaults={"name": name},
        )
        if not created and tenant.name != name:
            tenant.name = name
            tenant.save(update_fields=["name"])

        dom, dom_created = Domain.objects.get_or_create(
            domain=domain,
            defaults={"tenant": tenant, "is_primary": is_primary},
        )
        if not dom_created:
            updated = False
            if dom.tenant_id != tenant.id:
                dom.tenant = tenant
                updated = True
            if dom.is_primary != is_primary:
                dom.is_primary = is_primary
                updated = True
            if updated:
                dom.save(update_fields=["tenant", "is_primary"])

        self.stdout.write(
            f"tenant {'created' if created else 'exists'}: {schema_name}"
        )
        self.stdout.write(
            f"domain {'created' if dom_created else 'exists'}: {domain}"
        )
