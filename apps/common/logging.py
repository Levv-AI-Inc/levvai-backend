from apps.common.tenant_context import get_tenant_id


class TenantContextFilter:
    def filter(self, record):
        record.tenant_id = get_tenant_id()
        return True
