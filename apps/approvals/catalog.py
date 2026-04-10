from dataclasses import asdict, dataclass


TEXT_OPERATORS = (
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "in",
    "not_in",
    "is_blank",
    "is_not_blank",
)
NUMBER_OPERATORS = (
    "equals",
    "not_equals",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "in",
    "not_in",
    "is_blank",
    "is_not_blank",
)
BOOLEAN_OPERATORS = (
    "equals",
    "not_equals",
    "is_true",
    "is_false",
    "is_blank",
    "is_not_blank",
)
DATE_OPERATORS = (
    "equals",
    "not_equals",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "is_blank",
    "is_not_blank",
)

OPERATOR_DEFINITIONS = {
    "equals": {"label": "Equals", "value_required": True},
    "not_equals": {"label": "Does Not Equal", "value_required": True},
    "contains": {"label": "Contains", "value_required": True},
    "not_contains": {"label": "Does Not Contain", "value_required": True},
    "starts_with": {"label": "Starts With", "value_required": True},
    "ends_with": {"label": "Ends With", "value_required": True},
    "in": {"label": "In", "value_required": True},
    "not_in": {"label": "Not In", "value_required": True},
    "gt": {"label": "Greater Than", "value_required": True},
    "gte": {"label": "Greater Than Or Equal", "value_required": True},
    "lt": {"label": "Less Than", "value_required": True},
    "lte": {"label": "Less Than Or Equal", "value_required": True},
    "between": {"label": "Between", "value_required": True},
    "is_blank": {"label": "Is Blank", "value_required": False},
    "is_not_blank": {"label": "Is Not Blank", "value_required": False},
    "is_true": {"label": "Is True", "value_required": False},
    "is_false": {"label": "Is False", "value_required": False},
}


@dataclass(frozen=True)
class FieldDefinition:
    key: str
    label: str
    data_type: str
    supported_operators: tuple[str, ...]
    resolver_path: str
    description: str = ""
    dynamic: bool = False

    def as_dict(self):
        data = asdict(self)
        data["supported_operators"] = [
            {"key": key, **OPERATOR_DEFINITIONS[key]}
            for key in self.supported_operators
        ]
        return data


FIELD_DEFINITIONS = (
    FieldDefinition("job_title", "Job Title", "text", TEXT_OPERATORS, "job_title"),
    FieldDefinition("job_country", "Job Country", "text", TEXT_OPERATORS, "job_country"),
    FieldDefinition("job_region", "Job Region", "text", TEXT_OPERATORS, "job_region"),
    FieldDefinition("country", "Country", "text", TEXT_OPERATORS, "country"),
    FieldDefinition("region", "Region", "text", TEXT_OPERATORS, "region"),
    FieldDefinition("department", "Department", "text", TEXT_OPERATORS, "department"),
    FieldDefinition("commodity", "Commodity", "text", TEXT_OPERATORS, "commodity"),
    FieldDefinition("company_code", "Company Code", "text", TEXT_OPERATORS, "company_code"),
    FieldDefinition("contract_type", "Contract Type", "text", TEXT_OPERATORS, "contract_type"),
    FieldDefinition("contract_status", "Contract Status", "text", TEXT_OPERATORS, "contract_status"),
    FieldDefinition("engagement_type", "Engagement Type", "text", TEXT_OPERATORS, "engagement_type"),
    FieldDefinition("currency", "Currency", "text", TEXT_OPERATORS, "currency"),
    FieldDefinition("budget_amount", "Budget Amount", "number", NUMBER_OPERATORS, "budget_amount"),
    FieldDefinition("target_rate", "Target Rate", "number", NUMBER_OPERATORS, "target_rate"),
    FieldDefinition("worker_count", "Worker Count", "number", NUMBER_OPERATORS, "worker_count"),
    FieldDefinition("start_date", "Start Date", "date", DATE_OPERATORS, "start_date"),
    FieldDefinition("end_date", "End Date", "date", DATE_OPERATORS, "end_date"),
    FieldDefinition("cost_center_id", "Cost Center Id", "text", TEXT_OPERATORS, "cost_center_id"),
    FieldDefinition("cost_center_code", "Cost Center Code", "text", TEXT_OPERATORS, "cost_center.code"),
    FieldDefinition("cost_center_name", "Cost Center Name", "text", TEXT_OPERATORS, "cost_center.name"),
    FieldDefinition("cost_center_currency", "Cost Center Currency", "text", TEXT_OPERATORS, "cost_center.currency"),
    FieldDefinition(
        "cost_center_owner_email",
        "Cost Center Owner Email",
        "text",
        TEXT_OPERATORS,
        "cost_center.owner_email",
    ),
    FieldDefinition(
        "cost_center_business_unit_code",
        "Business Unit Code",
        "text",
        TEXT_OPERATORS,
        "cost_center.business_unit.code",
    ),
    FieldDefinition(
        "cost_center_business_unit_name",
        "Business Unit Name",
        "text",
        TEXT_OPERATORS,
        "cost_center.business_unit.name",
    ),
    FieldDefinition("site_id", "Site Id", "text", TEXT_OPERATORS, "site_id"),
    FieldDefinition("site_code", "Site Code", "text", TEXT_OPERATORS, "site.code"),
    FieldDefinition("site_name", "Site Name", "text", TEXT_OPERATORS, "site.name"),
    FieldDefinition("site_country", "Site Country", "text", TEXT_OPERATORS, "site.country"),
    FieldDefinition("site_region", "Site Region", "text", TEXT_OPERATORS, "site.state_province"),
    FieldDefinition("site_city", "Site City", "text", TEXT_OPERATORS, "site.city"),
    FieldDefinition("site_timezone", "Site Timezone", "text", TEXT_OPERATORS, "site.timezone"),
    FieldDefinition("site_currency", "Site Currency", "text", TEXT_OPERATORS, "site.currency"),
    FieldDefinition("legal_entity_id", "Legal Entity Id", "text", TEXT_OPERATORS, "legal_entity.id"),
    FieldDefinition("legal_entity_name", "Legal Entity Name", "text", TEXT_OPERATORS, "legal_entity.name"),
    FieldDefinition(
        "legal_entity_country",
        "Legal Entity Country",
        "text",
        TEXT_OPERATORS,
        "legal_entity.country",
    ),
    FieldDefinition(
        "legal_entity_currency",
        "Legal Entity Currency",
        "text",
        TEXT_OPERATORS,
        "legal_entity.currency",
    ),
    FieldDefinition("supplier_id", "Supplier Id", "text", TEXT_OPERATORS, "supplier_id"),
    FieldDefinition("supplier_name", "Supplier Name", "text", TEXT_OPERATORS, "supplier.name"),
    FieldDefinition("supplier_type", "Supplier Type", "text", TEXT_OPERATORS, "supplier.supplier_type"),
    FieldDefinition("supplier_category", "Supplier Category", "text", TEXT_OPERATORS, "supplier.category"),
    FieldDefinition("supplier_status", "Supplier Status", "text", TEXT_OPERATORS, "supplier.status"),
    FieldDefinition("supplier_risk_level", "Supplier Risk Level", "text", TEXT_OPERATORS, "supplier.risk_level"),
    FieldDefinition(
        "supplier_compliance_status",
        "Supplier Compliance Status",
        "text",
        TEXT_OPERATORS,
        "supplier.compliance_status",
    ),
    FieldDefinition("created_by", "Created By", "text", TEXT_OPERATORS, "created_by"),
    FieldDefinition("submitted_by", "Submitted By", "text", TEXT_OPERATORS, "submitted_by"),
    FieldDefinition("is_urgent", "Is Urgent", "boolean", BOOLEAN_OPERATORS, "is_urgent"),
    FieldDefinition(
        "custom_fields.*",
        "Custom Field",
        "dynamic",
        (
            "equals",
            "not_equals",
            "contains",
            "not_contains",
            "in",
            "not_in",
            "gt",
            "gte",
            "lt",
            "lte",
            "between",
            "is_blank",
            "is_not_blank",
            "is_true",
            "is_false",
        ),
        "custom_fields",
        dynamic=True,
    ),
)

FIELD_DEFINITION_BY_KEY = {field.key: field for field in FIELD_DEFINITIONS if not field.dynamic}
CUSTOM_FIELD_DEFINITION = next(field for field in FIELD_DEFINITIONS if field.dynamic)


def get_field_definition(field_key):
    if field_key in FIELD_DEFINITION_BY_KEY:
        return FIELD_DEFINITION_BY_KEY[field_key]
    if is_custom_field_key(field_key):
        suffix = field_key.partition("custom_fields.")[2]
        return FieldDefinition(
            key=field_key,
            label=f"Custom Field: {suffix}",
            data_type="dynamic",
            supported_operators=CUSTOM_FIELD_DEFINITION.supported_operators,
            resolver_path=field_key,
            description="Dynamically resolved custom field value.",
            dynamic=True,
        )
    return None


def is_custom_field_key(field_key):
    return isinstance(field_key, str) and field_key.startswith("custom_fields.") and len(field_key) > len("custom_fields.")


def build_catalog_response():
    return {
        "operators": [
            {"key": key, **value}
            for key, value in OPERATOR_DEFINITIONS.items()
        ],
        "fields": [field.as_dict() for field in FIELD_DEFINITIONS],
    }

