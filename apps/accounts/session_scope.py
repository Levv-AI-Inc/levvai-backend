SESSION_TENANT_ID_KEY = "tenant_id"


def bind_session_to_tenant(request, tenant):
    """Bind the authenticated session to a single tenant."""
    if tenant is None or not hasattr(request, "session"):
        return
    request.session[SESSION_TENANT_ID_KEY] = tenant.id
    request.session.modified = True


def is_session_bound_to_tenant(request, tenant):
    """Return True only when the session is explicitly bound to the tenant."""
    if tenant is None or not hasattr(request, "session"):
        return False
    actual = request.session.get(SESSION_TENANT_ID_KEY)
    if actual is None:
        return False
    return str(actual) == str(tenant.id)
