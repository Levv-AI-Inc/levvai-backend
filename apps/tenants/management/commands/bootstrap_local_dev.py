import getpass
import os
import sys

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.accounts.models import Membership
from apps.accounts.password_policy import record_password_history
from apps.tenants.models import Domain, Tenant


class Command(BaseCommand):
    help = (
        "Create an idempotent localhost tenant, map localhost domains, "
        "and create or promote a local admin user."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            default="local",
            help="Tenant schema name (default: local).",
        )
        parser.add_argument(
            "--name",
            default="Local Development",
            help="Tenant display name (default: Local Development).",
        )
        parser.add_argument(
            "--email",
            default=os.getenv("LOCAL_ADMIN_EMAIL", "admin@local.levvai.test"),
            help="Local admin email.",
        )
        parser.add_argument(
            "--password",
            default=os.getenv("LOCAL_ADMIN_PASSWORD"),
            help=(
                "Local admin password. Prefer omitting this option so the "
                "command prompts without storing the password in shell history."
            ),
        )
        parser.add_argument(
            "--first-name",
            default="Local",
            help="Local admin first name.",
        )
        parser.add_argument(
            "--last-name",
            default="Admin",
            help="Local admin last name.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow a non-debug or remotely hosted development database.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "bootstrap_local_dev requires DJANGO_DEBUG=true. "
                "Use --force only when you have verified DATABASE_URL points "
                "to a disposable development database."
            )

        database_host = (
            settings.DATABASES.get("default", {}).get("HOST", "") or ""
        ).strip().lower()
        local_database_hosts = {"", "localhost", "127.0.0.1", "::1"}
        if database_host not in local_database_hosts and not options["force"]:
            raise CommandError(
                "bootstrap_local_dev detected a remotely hosted database. "
                "Use a local disposable PostgreSQL database, or pass --force "
                "only for an explicitly verified remote development database."
            )

        schema_name = options["schema"].strip().lower()
        tenant_name = options["name"].strip()
        email = options["email"].strip().lower()
        password = options.get("password")

        if not schema_name or schema_name == "public":
            raise CommandError("The local tenant schema must be non-public.")
        if not tenant_name:
            raise CommandError("Tenant name cannot be empty.")
        if not email:
            raise CommandError("Admin email cannot be empty.")

        if not password:
            if not sys.stdin.isatty():
                raise CommandError(
                    "No password was provided. Set LOCAL_ADMIN_PASSWORD or "
                    "run the command interactively."
                )
            password = getpass.getpass("Local admin password: ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                raise CommandError("Passwords do not match.")

        if not password:
            raise CommandError("Admin password cannot be empty.")

        tenant, tenant_created = Tenant.objects.get_or_create(
            schema_name=schema_name,
            defaults={"name": tenant_name},
        )
        if not tenant_created and tenant.name != tenant_name:
            tenant.name = tenant_name
            tenant.save(update_fields=["name"])

        domain_results = []
        for index, hostname in enumerate(("localhost", "127.0.0.1")):
            domain, domain_created = Domain.objects.get_or_create(
                domain=hostname,
                defaults={
                    "tenant": tenant,
                    "is_primary": index == 0,
                },
            )
            changed_fields = []
            if domain.tenant_id != tenant.id:
                domain.tenant = tenant
                changed_fields.append("tenant")
            if domain.is_primary != (index == 0):
                domain.is_primary = index == 0
                changed_fields.append("is_primary")
            if changed_fields:
                domain.save(update_fields=changed_fields)
            domain_results.append((hostname, domain_created, bool(changed_fields)))

        User = get_user_model()
        with transaction.atomic():
            user = (
                User.objects.filter(
                    Q(email__iexact=email) | Q(username__iexact=email)
                )
                .order_by("id")
                .first()
            )
            user_created = user is None
            if user is None:
                user = User(username=email, email=email)

            user.username = email
            user.email = email
            user.first_name = options["first_name"].strip()
            user.last_name = options["last_name"].strip()
            user.auth_type = User.AUTH_PASSWORD
            user.is_active = True
            user.set_password(password)
            user.save()

            membership, membership_created = Membership.objects.get_or_create(
                user=user,
                tenant=tenant,
                defaults={
                    "role": Membership.ROLE_ADMIN,
                    "status": Membership.STATUS_ACTIVE,
                    "is_active": True,
                },
            )
            membership.role = Membership.ROLE_ADMIN
            membership.status = Membership.STATUS_ACTIVE
            membership.is_active = True
            membership.supplier_id = None
            membership.full_clean()
            membership.save()
            record_password_history(user, tenant)

        self.stdout.write(
            self.style.SUCCESS(
                f"Tenant {'created' if tenant_created else 'ready'}: "
                f"{tenant.schema_name} ({tenant.name})"
            )
        )
        for hostname, created, updated in domain_results:
            state = "created" if created else "updated" if updated else "ready"
            self.stdout.write(f"Domain {state}: {hostname}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Admin {'created' if user_created else 'updated'}: {email}"
            )
        )
        self.stdout.write(
            f"Membership {'created' if membership_created else 'updated'}: admin"
        )
        self.stdout.write("Login URL: http://localhost:3000/auth/login")
