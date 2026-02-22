from django.core.management.base import BaseCommand

from apps.tenants.models import Domain, Tenant


class Command(BaseCommand):
    help = "Create localhost/127.0.0.1 domains for the public tenant."

    def handle(self, *args, **options):
        try:
            tenant = Tenant.objects.get(schema_name="public")
        except Tenant.DoesNotExist:
            self.stderr.write("Public tenant not found (schema_name='public').")
            return

        domains = ["localhost", "127.0.0.1"]
        for idx, domain in enumerate(domains):
            obj, created = Domain.objects.get_or_create(
                domain=domain,
                defaults={"tenant": tenant, "is_primary": idx == 0},
            )
            if not created and obj.tenant_id != tenant.id:
                obj.tenant = tenant
                obj.is_primary = idx == 0
                obj.save(update_fields=["tenant", "is_primary"])

            status = "created" if created else "exists"
            self.stdout.write(f"{status}: {domain}")
