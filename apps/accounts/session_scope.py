SESSION_TENANT_ID_KEY = "tenant_id"
SESSION_MEMBERSHIP_ID_KEY = "membership_id"
SESSION_AUTHORIZATION_VERSION_KEY = "membership_authorization_version"


def bind_session_to_tenant(request, tenant, membership=None):
    """Bind the authenticated session to one tenant membership version."""
    if tenant is None or not hasattr(request, "session"):
        return
    request.session[SESSION_TENANT_ID_KEY] = tenant.id
    if membership is not None:
        request.session[SESSION_MEMBERSHIP_ID_KEY] = membership.id
        request.session[SESSION_AUTHORIZATION_VERSION_KEY] = membership.authorization_version
    request.session.modified = True


def is_session_bound_to_tenant(request, tenant, membership=None):
    """Return True when tenant and, if supplied, membership version match.

    Sessions created before authorization versioning are upgraded on first use
    so the additive migration does not log out every active tenant session.
    """
    if tenant is None or not hasattr(request, "session"):
        return False
    actual = request.session.get(SESSION_TENANT_ID_KEY)
    if actual is None:
        return False
    if str(actual) != str(tenant.id):
        return False
    if membership is None:
        return True

    bound_membership_id = request.session.get(SESSION_MEMBERSHIP_ID_KEY)
    bound_version = request.session.get(SESSION_AUTHORIZATION_VERSION_KEY)
    if bound_membership_id is None and bound_version is None:
        bind_session_to_tenant(request, tenant, membership)
        return True
    if bound_membership_id is None or bound_version is None:
        return False
    return (
        str(bound_membership_id) == str(membership.id)
        and str(bound_version) == str(membership.authorization_version)
    )
