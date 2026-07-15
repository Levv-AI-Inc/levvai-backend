from collections import Counter
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_tenants.utils import schema_context

from apps.accounts.models import Membership
from apps.approvals.models import (
    ApprovalChain,
    ApprovalChainCondition,
    ApprovalChainStep,
)
from apps.masterdata.models import (
    BusinessUnit,
    Company,
    CostCenter,
    JobTemplate,
    LegalEntity,
    Location,
    RoleDefinition,
    Site,
    Supplier,
)
from apps.policies.models import (
    WorkerLifecycleWorkflow,
    WorkflowBlock,
    WorkflowBlockRequirement,
    WorkflowPolicyScope,
    WorkflowPolicyScopeField,
    WorkflowRequirement,
)
from apps.rates.calculations import calculate_bill_rate_for_structure
from apps.rates.models import (
    RateCard,
    RateCardLine,
    RateCardLineValue,
    RateRule,
    RateRuleCondition,
    RateStructure,
    RateStructureComponent,
)
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "Seed realistic, linked demo data into a local development tenant."

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            default="local",
            help="Tenant schema name (default: local).",
        )
        parser.add_argument(
            "--admin-email",
            default="admin@local.levvai.test",
            help="Existing tenant admin used as record owner and approver.",
        )
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="Update existing seed-owned records to the current seed values.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow seeding a non-debug or remotely hosted development database.",
        )

    def handle(self, *args, **options):
        self._assert_safe_database(options["force"])
        self.refresh = options["refresh"]
        self.created = Counter()
        self.existing = Counter()

        schema_name = options["schema"].strip().lower()
        email = options["admin_email"].strip().lower()
        if not schema_name or schema_name == "public":
            raise CommandError("Seed data must target a non-public tenant schema.")

        try:
            tenant = Tenant.objects.get(schema_name=schema_name)
        except Tenant.DoesNotExist as exc:
            raise CommandError(
                f"Tenant schema '{schema_name}' does not exist. Run bootstrap_local_dev first."
            ) from exc

        membership = (
            Membership.objects.select_related("user")
            .filter(
                tenant=tenant,
                user__email__iexact=email,
                role=Membership.ROLE_ADMIN,
                status=Membership.STATUS_ACTIVE,
                is_active=True,
            )
            .first()
        )
        if membership is None:
            raise CommandError(
                f"Active admin membership for '{email}' was not found in tenant '{schema_name}'."
            )

        with schema_context(schema_name), transaction.atomic():
            context = self._seed_master_data(membership.user)
            self._seed_approval_chains(membership.user)
            self._seed_rates(context)
            self._seed_workflows(tenant, membership.user, context)

        mode = "refreshed" if self.refresh else "preserved when already present"
        self.stdout.write(self.style.SUCCESS(f"Local demo data ready in '{schema_name}' ({mode})."))
        for model_name in sorted(set(self.created) | set(self.existing)):
            self.stdout.write(
                f"  {model_name}: {self.created[model_name]} created, "
                f"{self.existing[model_name]} existing"
            )
        self.stdout.write(
            "  Subsidiary examples are represented by the seeded Canada and UK legal entities."
        )

    def _assert_safe_database(self, force):
        if not settings.DEBUG and not force:
            raise CommandError(
                "seed_local_data requires DJANGO_DEBUG=true. Use --force only for a "
                "verified disposable development database."
            )
        database_host = (
            settings.DATABASES.get("default", {}).get("HOST", "") or ""
        ).strip().lower()
        if database_host not in {"", "localhost", "127.0.0.1", "::1"} and not force:
            raise CommandError(
                "seed_local_data detected a remotely hosted database. Use a local "
                "disposable PostgreSQL database or explicitly pass --force."
            )

    def _ensure(self, model, lookup, defaults):
        obj, created = model.objects.get_or_create(defaults=defaults, **lookup)
        if created or self.refresh:
            for field, value in defaults.items():
                setattr(obj, field, value)
            obj.full_clean()
            obj.save()
        counter = self.created if created else self.existing
        counter[model.__name__] += 1
        return obj

    def _ensure_workflow_block(self, workflow, sequence, defaults):
        obj, created = WorkflowBlock.objects.get_or_create(
            workflow=workflow,
            sequence=sequence,
            defaults=defaults,
        )
        needs_update = created or self.refresh or self._has_seed_drift(obj, defaults)
        if needs_update:
            for field, value in defaults.items():
                setattr(obj, field, value)
            obj.full_clean()
            obj.save()
            if obj.block_type == WorkflowBlock.TYPE_SYSTEM:
                obj.requirements.all().delete()
        counter = self.created if created else self.existing
        counter[WorkflowBlock.__name__] += 1
        return obj

    def _ensure_workflow_block_requirement(self, block, sequence, defaults):
        obj, created = WorkflowBlockRequirement.objects.get_or_create(
            block=block,
            sequence=sequence,
            defaults=defaults,
        )
        needs_update = created or self.refresh or self._has_seed_drift(obj, defaults)
        if needs_update:
            for field, value in defaults.items():
                setattr(obj, field, value)
            obj.full_clean()
            obj.save()
        counter = self.created if created else self.existing
        counter[WorkflowBlockRequirement.__name__] += 1
        return obj

    def _has_seed_drift(self, obj, defaults):
        return any(getattr(obj, field) != value for field, value in defaults.items())

    def _seed_master_data(self, admin):
        company = self._ensure(
            Company,
            {"name": "LevvAI Demo Corporation"},
            {},
        )

        entities = {}
        entity_rows = [
            (
                "LEV-US",
                {
                    "name": "LevvAI US Holdings",
                    "country": "US",
                    "tax_id": "US-DEMO-10001",
                    "currency": "USD",
                    "erp_code": "US01",
                    "billing_address": {
                        "line1": "350 Fifth Avenue",
                        "city": "New York",
                        "region": "NY",
                        "postal_code": "10118",
                        "country": "US",
                    },
                    "status": LegalEntity.STATUS_ACTIVE,
                },
            ),
            (
                "LEV-CA",
                {
                    "name": "LevvAI Canada ULC",
                    "country": "CA",
                    "tax_id": "CA-DEMO-20002",
                    "currency": "CAD",
                    "erp_code": "CA01",
                    "billing_address": {
                        "line1": "100 King Street West",
                        "city": "Toronto",
                        "region": "ON",
                        "postal_code": "M5X 1A9",
                        "country": "CA",
                    },
                    "status": LegalEntity.STATUS_ACTIVE,
                },
            ),
            (
                "LEV-UK",
                {
                    "name": "LevvAI UK Ltd",
                    "country": "GB",
                    "tax_id": "GB-DEMO-30003",
                    "currency": "GBP",
                    "erp_code": "UK01",
                    "billing_address": {
                        "line1": "1 Canada Square",
                        "city": "London",
                        "region": "England",
                        "postal_code": "E14 5AB",
                        "country": "GB",
                    },
                    "status": LegalEntity.STATUS_ACTIVE,
                },
            ),
        ]
        for entity_id, defaults in entity_rows:
            entities[entity_id] = self._ensure(LegalEntity, {"id": entity_id}, defaults)

        business_units = {}
        business_unit_rows = [
            ("TECH", "Technology", None, "LEV-US"),
            ("ENG", "Engineering", "TECH", "LEV-US"),
            ("DATA-AI", "Data & AI", "TECH", "LEV-US"),
            ("OPS", "Workforce Operations", None, "LEV-US"),
            ("FIN", "Finance", None, "LEV-US"),
        ]
        for code, name, parent_code, legal_entity_id in business_unit_rows:
            business_units[code] = self._ensure(
                BusinessUnit,
                {"code": code},
                {
                    "name": name,
                    "parent": business_units.get(parent_code),
                    "description": f"{name} business unit",
                    "legal_entity_id": legal_entity_id,
                    "gl_account_id": f"GL-{code}",
                    "status": BusinessUnit.STATUS_ACTIVE,
                    "company": company,
                },
            )

        cost_centers = {}
        cost_center_rows = [
            ("ENG-100", "Software Engineering", "ENG", "USD", "2500000.00", "LEV-US"),
            ("DATA-110", "Data & AI", "DATA-AI", "USD", "1800000.00", "LEV-US"),
            ("OPS-200", "Workforce Operations", "OPS", "USD", "1200000.00", "LEV-US"),
            ("FIN-300", "Corporate Finance", "FIN", "USD", "900000.00", "LEV-US"),
        ]
        for code, name, unit_code, currency, budget, legal_entity_id in cost_center_rows:
            cost_centers[code] = self._ensure(
                CostCenter,
                {"code": code},
                {
                    "name": name,
                    "description": f"Demo cost center for {name}",
                    "owner_email": admin.email,
                    "business_unit": business_units[unit_code],
                    "currency": currency,
                    "status": CostCenter.STATUS_ACTIVE,
                    "budget_amount": Decimal(budget),
                    "budget_period": CostCenter.PERIOD_ANNUAL,
                    "gl_account_id": f"GL-{code}",
                    "erp_code": code,
                    "legal_entity_id": legal_entity_id,
                },
            )

        locations = {}
        for name, country, region in [
            ("New York", "USA", "North America"),
            ("Austin", "USA", "North America"),
            ("Toronto", "Canada", "North America"),
            ("London", "UK", "EMEA"),
        ]:
            locations[name] = self._ensure(
                Location,
                {"name": name, "country": country, "region": region},
                {"status": Location.STATUS_ACTIVE},
            )

        site_rows = [
            (
                "NYC-HQ",
                "New York Headquarters",
                "350 Fifth Avenue",
                "New York",
                "NY",
                "US",
                "10118",
                "America/New_York",
                "USD",
                "LEV-US",
            ),
            (
                "TOR-HUB",
                "Toronto Delivery Hub",
                "100 King Street West",
                "Toronto",
                "ON",
                "CA",
                "M5X 1A9",
                "America/Toronto",
                "CAD",
                "LEV-CA",
            ),
            (
                "LON-HUB",
                "London Delivery Hub",
                "1 Canada Square",
                "London",
                "England",
                "GB",
                "E14 5AB",
                "Europe/London",
                "GBP",
                "LEV-UK",
            ),
        ]
        for code, name, address, city, state, country, postal, timezone, currency, entity_id in site_rows:
            self._ensure(
                Site,
                {"code": code},
                {
                    "name": name,
                    "status": Site.STATUS_ACTIVE,
                    "address_line1": address,
                    "address_line2": "",
                    "city": city,
                    "state_province": state,
                    "country": country,
                    "postal_code": postal,
                    "timezone": timezone,
                    "hours_per_day": Decimal("8.00"),
                    "hours_per_week": Decimal("40.00"),
                    "currency": currency,
                    "legal_entity": entities[entity_id],
                    "tax_config": {"source": "demo"},
                    "erp_code": code,
                },
            )

        suppliers = {}
        supplier_rows = [
            (
                "SUP-APEX",
                "Apex Talent Partners",
                Supplier.TYPE_STAFFING,
                Supplier.RISK_LOW,
                Supplier.COMPLIANCE_COMPLIANT,
                42,
                1,
            ),
            (
                "SUP-NORTH",
                "Northstar Consulting",
                Supplier.TYPE_BOTH,
                Supplier.RISK_LOW,
                Supplier.COMPLIANCE_COMPLIANT,
                18,
                3,
            ),
            (
                "SUP-GTR",
                "Global Tech Resources",
                Supplier.TYPE_STAFFING,
                Supplier.RISK_MEDIUM,
                Supplier.COMPLIANCE_REVIEW_REQUIRED,
                27,
                0,
            ),
        ]
        for code, name, supplier_type, risk, compliance, workers, sows in supplier_rows:
            suppliers[code] = self._ensure(
                Supplier,
                {"supplier_code": code, "name": name},
                {
                    "email": f"hello@{code.lower().replace('sup-', '')}.example",
                    "contact_name": f"{name} Account Team",
                    "contact_email": f"account@{code.lower().replace('sup-', '')}.example",
                    "contact_phone": "+1 555 010 0100",
                    "tax_id": f"DEMO-{code}",
                    "diversity_status": "Certified",
                    "supplier_type": supplier_type,
                    "category": "Technology Talent",
                    "owner_name": admin.get_full_name() or admin.email,
                    "status": Supplier.STATUS_ACTIVE,
                    "risk_level": risk,
                    "compliance_status": compliance,
                    "active_workers": workers,
                    "active_sows": sows,
                },
            )

        roles = {}
        role_rows = [
            ("ROLE-SWE-NYC", "Software Engineer", "US", "New York", "New York", "USD"),
            ("ROLE-DATA-NYC", "Data Engineer", "US", "New York", "New York", "USD"),
            ("ROLE-PM-TOR", "Project Manager", "CA", "Ontario", "Toronto", "CAD"),
            ("ROLE-BA-LON", "Business Analyst", "GB", "England", "London", "GBP"),
        ]
        for code, name, country, region, city, currency in role_rows:
            roles[code] = self._ensure(
                RoleDefinition,
                {"code": code},
                {
                    "name": name,
                    "description": f"Demo {name} role for {city}",
                    "country": country,
                    "region": region,
                    "city": city,
                    "default_currency": currency,
                    "default_unit": RoleDefinition.UNIT_HOUR,
                    "is_active": True,
                },
            )
            self._ensure(
                JobTemplate,
                {"role": name, "country": country, "region_in_country": region},
                {"description": f"Standard {name} engagement template."},
            )

        return {
            "business_units": business_units,
            "cost_centers": cost_centers,
            "locations": locations,
            "suppliers": suppliers,
            "roles": roles,
        }

    def _seed_approval_chains(self, admin):
        chain_rows = [
            (
                "Standard US Contingent Approval",
                "Default approval route for US contingent worker requests.",
                10,
                [("job_country", "equals", "US")],
                Decimal("0.00"),
            ),
            (
                "High Value Engagement Approval",
                "Approval route for requests with a budget of at least USD 100,000.",
                20,
                [
                    ("currency", "equals", "USD"),
                    ("budget_amount", "gte", "100000"),
                ],
                Decimal("100000.00"),
            ),
        ]
        for name, description, priority, conditions, amount in chain_rows:
            chain = self._ensure(
                ApprovalChain,
                {"name": name},
                {
                    "description": description,
                    "is_active": True,
                    "priority": priority,
                    "match_strategy": ApprovalChain.MATCH_ALL,
                },
            )
            for sequence, (field_key, operator, value) in enumerate(conditions, start=1):
                self._ensure(
                    ApprovalChainCondition,
                    {"approval_chain": chain, "sequence": sequence},
                    {"field_key": field_key, "operator": operator, "value_json": value},
                )
            self._ensure(
                ApprovalChainStep,
                {"approval_chain": chain, "sequence": 1},
                {
                    "step_type": ApprovalChainStep.TYPE_SPECIFIC_USER,
                    "approver": admin,
                    "amount": amount,
                    "currency": "USD",
                },
            )

    def _seed_rates(self, context):
        structure = self._ensure(
            RateStructure,
            {"name": "Standard US Contingent Markup"},
            {
                "description": "Base pay plus supplier markup, statutory cost, and fixed admin fee.",
                "status": RateStructure.STATUS_ACTIVE,
                "currency_mode": RateStructure.CURRENCY_MODE_SINGLE,
                "rounding_scale": 2,
                "is_default": True,
            },
        )
        components = {}
        component_rows = [
            (
                1,
                "base_pay",
                "Base Pay",
                RateStructureComponent.VALUE_CURRENCY,
                RateStructureComponent.ROLE_BASE,
                True,
            ),
            (
                2,
                "supplier_markup",
                "Supplier Markup",
                RateStructureComponent.VALUE_PERCENTAGE,
                RateStructureComponent.ROLE_ADDITIVE_PERCENT,
                True,
            ),
            (
                3,
                "statutory_cost",
                "Statutory Cost",
                RateStructureComponent.VALUE_PERCENTAGE,
                RateStructureComponent.ROLE_ADDITIVE_PERCENT,
                True,
            ),
            (
                4,
                "admin_fee",
                "Admin Fee",
                RateStructureComponent.VALUE_CURRENCY,
                RateStructureComponent.ROLE_ADDITIVE_AMOUNT,
                False,
            ),
        ]
        for sequence, code, label, value_type, calculation_role, required in component_rows:
            components[code] = self._ensure(
                RateStructureComponent,
                {"rate_structure": structure, "code": code},
                {
                    "sequence": sequence,
                    "label": label,
                    "value_type": value_type,
                    "calculation_role": calculation_role,
                    "is_required": required,
                    "is_active": True,
                },
            )

        cards = [
            (
                "Software Engineer - New York 2026",
                context["roles"]["ROLE-SWE-NYC"],
                [
                    (context["suppliers"]["SUP-APEX"], "75.00", "18.00", "12.00", "2.00"),
                    (context["suppliers"]["SUP-NORTH"], "78.00", "16.00", "12.00", "2.00"),
                ],
            ),
            (
                "Data Engineer - New York 2026",
                context["roles"]["ROLE-DATA-NYC"],
                [
                    (context["suppliers"]["SUP-GTR"], "82.00", "20.00", "12.00", "2.00"),
                ],
            ),
        ]
        for card_name, role, line_rows in cards:
            card = self._ensure(
                RateCard,
                {"name": card_name, "role_definition": role},
                {
                    "currency": "USD",
                    "unit": RateCard.UNIT_HOUR,
                    "effective_date": date(2026, 1, 1),
                    "end_date": date(2026, 12, 31),
                    "rate_structure": structure,
                    "status": RateCard.STATUS_ACTIVE,
                    "notes": "Local demo rate card.",
                },
            )
            for sequence, (supplier, base, markup, statutory, fee) in enumerate(line_rows, start=1):
                values = {
                    "base_pay": Decimal(base),
                    "supplier_markup": Decimal(markup),
                    "statutory_cost": Decimal(statutory),
                    "admin_fee": Decimal(fee),
                }
                result = calculate_bill_rate_for_structure(
                    rate_structure=structure,
                    component_values=values,
                )
                line = self._ensure(
                    RateCardLine,
                    {
                        "rate_card": card,
                        "supplier": supplier,
                        "location_label": "New York",
                    },
                    {
                        "sequence": sequence,
                        "bill_rate": result["bill_rate"],
                    },
                )
                for code, numeric_value in values.items():
                    self._ensure(
                        RateCardLineValue,
                        {
                            "rate_card_line": line,
                            "rate_structure_component": components[code],
                        },
                        {"numeric_value": numeric_value},
                    )

        rule_rows = [
            (
                "Overtime Premium - US Technology",
                10,
                "hours",
                "gt",
                "40",
                RateRule.ACTION_MULTIPLY_BILL_RATE,
                Decimal("1.5000"),
            ),
            (
                "Holiday Premium - US Technology",
                20,
                "is_holiday",
                "equals",
                True,
                RateRule.ACTION_MULTIPLY_BILL_RATE,
                Decimal("2.0000"),
            ),
        ]
        for name, priority, field_key, operator, value, action, action_value in rule_rows:
            rule = self._ensure(
                RateRule,
                {"name": name, "role_definition": context["roles"]["ROLE-SWE-NYC"]},
                {
                    "description": f"Demo rule for {name.lower()}.",
                    "priority": priority,
                    "status": RateRule.STATUS_ACTIVE,
                    "rate_structure": structure,
                    "effective_date": date(2026, 1, 1),
                    "end_date": None,
                    "action_type": action,
                    "action_value": action_value,
                    "stop_processing": True,
                },
            )
            self._ensure(
                RateRuleCondition,
                {"rate_rule": rule, "sequence": 1},
                {
                    "joiner": RateRuleCondition.JOIN_AND,
                    "field_key": field_key,
                    "operator": operator,
                    "value_json": value,
                },
            )

    def _seed_workflows(self, tenant, admin, context):
        requirements = {}
        requirement_rows = [
            ("government_id", "Government ID Photo Check", WorkflowRequirement.OWNER_WORKER),
            ("background_screening", "Background Screening", WorkflowRequirement.OWNER_WORKER),
            ("signed_nda", "Signed Non-Disclosure Agreement", WorkflowRequirement.OWNER_WORKER),
            ("equipment_issue", "Equipment Provisioning", WorkflowRequirement.OWNER_IT),
            ("equipment_return", "Equipment Return", WorkflowRequirement.OWNER_WORKER),
            ("access_revoke", "Access Revocation", WorkflowRequirement.OWNER_IT),
        ]
        for code, name, owner in requirement_rows:
            requirements[code] = self._ensure(
                WorkflowRequirement,
                {"code": code},
                {
                    "tenant_id": tenant.id,
                    "name": name,
                    "description": f"Demo lifecycle requirement: {name}.",
                    "default_owner": owner,
                    "is_active": True,
                },
            )

        onboarding = self._ensure(
            WorkerLifecycleWorkflow,
            {
                "tenant_id": tenant.id,
                "name": "US Technology Contractor Onboarding",
                "workflow_type": WorkerLifecycleWorkflow.TYPE_ONBOARDING,
            },
            {
                "status": WorkerLifecycleWorkflow.STATUS_PUBLISHED,
                "is_active": True,
                "version": 1,
                "created_by": admin,
            },
        )
        onboarding_scope = self._ensure(
            WorkflowPolicyScope,
            {"workflow": onboarding},
            {"worker_type": WorkflowPolicyScope.WORKER_TYPE_CONTINGENT},
        )
        self._ensure(
            WorkflowPolicyScopeField,
            {"scope": onboarding_scope, "field_key": WorkflowPolicyScopeField.FIELD_LOCATION},
            {
                "sequence": 1,
                "operator": WorkflowPolicyScopeField.OPERATOR_EQUALS,
                "location": context["locations"]["New York"],
            },
        )
        self._ensure(
            WorkflowPolicyScopeField,
            {"scope": onboarding_scope, "field_key": WorkflowPolicyScopeField.FIELD_ROLE},
            {
                "sequence": 2,
                "operator": WorkflowPolicyScopeField.OPERATOR_EQUALS,
                "role_definition": context["roles"]["ROLE-SWE-NYC"],
            },
        )
        self._seed_requirement_block(
            onboarding,
            1,
            "Identity & Eligibility",
            WorkflowBlock.GATE_HARD,
            [requirements["government_id"], requirements["background_screening"]],
        )
        self._seed_requirement_block(
            onboarding,
            2,
            "Legal & Compliance",
            WorkflowBlock.GATE_SOFT,
            [requirements["signed_nda"]],
        )
        self._seed_system_block(
            onboarding,
            3,
            "Account & Equipment Provisioning",
            WorkflowBlock.GATE_SOFT,
            "create_worker_accounts",
        )

        offboarding = self._ensure(
            WorkerLifecycleWorkflow,
            {
                "tenant_id": tenant.id,
                "name": "US Contractor Offboarding",
                "workflow_type": WorkerLifecycleWorkflow.TYPE_OFFBOARDING,
            },
            {
                "status": WorkerLifecycleWorkflow.STATUS_PUBLISHED,
                "is_active": True,
                "version": 1,
                "created_by": admin,
            },
        )
        offboarding_scope = self._ensure(
            WorkflowPolicyScope,
            {"workflow": offboarding},
            {"worker_type": WorkflowPolicyScope.WORKER_TYPE_CONTINGENT},
        )
        self._ensure(
            WorkflowPolicyScopeField,
            {
                "scope": offboarding_scope,
                "field_key": WorkflowPolicyScopeField.FIELD_BUSINESS_UNIT,
            },
            {
                "sequence": 1,
                "operator": WorkflowPolicyScopeField.OPERATOR_EQUALS,
                "business_unit": context["business_units"]["TECH"],
            },
        )
        self._seed_requirement_block(
            offboarding,
            1,
            "Worker Exit Checklist",
            WorkflowBlock.GATE_HARD,
            [requirements["equipment_return"]],
        )
        self._seed_system_block(
            offboarding,
            2,
            "Revoke System Access",
            WorkflowBlock.GATE_HARD,
            "revoke_worker_access",
        )

    def _seed_requirement_block(self, workflow, sequence, name, gate_type, requirements):
        block = self._ensure_workflow_block(
            workflow,
            sequence,
            {
                "block_type": WorkflowBlock.TYPE_REQUIREMENT,
                "name": name,
                "gate_type": gate_type,
                "integration_type": "",
                "config": {},
            },
        )
        for requirement_sequence, requirement in enumerate(requirements, start=1):
            self._ensure_workflow_block_requirement(
                block,
                requirement_sequence,
                {
                    "requirement": requirement,
                    "name": requirement.name,
                    "owner": requirement.default_owner,
                    "config": {},
                },
            )

    def _seed_system_block(self, workflow, sequence, name, gate_type, endpoint_key):
        self._ensure_workflow_block(
            workflow,
            sequence,
            {
                "block_type": WorkflowBlock.TYPE_SYSTEM,
                "name": name,
                "gate_type": gate_type,
                "integration_type": WorkflowBlock.INTEGRATION_API_CALL,
                "config": {"endpoint_key": endpoint_key},
            },
        )
