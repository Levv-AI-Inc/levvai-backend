import threading

_local = threading.local()


def set_tenant_id(tenant_id):
    _local.tenant_id = tenant_id


def get_tenant_id():
    return getattr(_local, "tenant_id", None)
