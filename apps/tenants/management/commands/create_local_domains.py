from django.core.management.base import BaseCommand, CommandError

from apps.tenants.models import Domain, Tenant


class Command(BaseCommand):
    help = "Map localhost/127.0.0.1 to a non-public development tenant."

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            default="local",
            help="Existing non-public tenant schema (default: local).",
        )

    def handle(self, *args, **options):
        schema_name = options["schema"].strip().lower()
        if not schema_name or schema_name == "public":
            raise CommandError("Local domains must map to a non-public tenant.")

        try:
            tenant = Tenant.objects.get(schema_name=schema_name)
        except Tenant.DoesNotExist:
            raise CommandError(
                f"Tenant '{schema_name}' does not exist. "
                "Run bootstrap_local_dev first."
            )

        domains = ["localhost", "127.0.0.1"]
        for idx, domain in enumerate(domains):
            obj, created = Domain.objects.get_or_create(
                domain=domain,
                defaults={"tenant": tenant, "is_primary": idx == 0},
            )
            if not created and (
                obj.tenant_id != tenant.id or obj.is_primary != (idx == 0)
            ):
                obj.tenant = tenant
                obj.is_primary = idx == 0
                obj.save(update_fields=["tenant", "is_primary"])

            status = "created" if created else "exists"
            self.stdout.write(f"{status}: {domain}")
