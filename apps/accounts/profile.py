from urllib.parse import urlparse

from apps.accounts.models import Membership, WorkerEngagement, WorkerProfile


PROFILE_INTERNAL = "internal"
PROFILE_SUPPLIER = "supplier"
PROFILE_WORKER = "worker"

DEFAULT_APP_HOME_PATH = "/home"
WORKER_HOME_PATH = "/external/act-as-worker/timesheet"
WORKER_PROFILE_ROOT = "/external/act-as-worker"
SESSION_WORKER_ENGAGEMENT_ID_KEY = "worker_engagement_id"

NON_WORKER_TENANT_ROLES = (
    Membership.ROLE_ADMIN,
    Membership.ROLE_MANAGER,
    Membership.ROLE_BUSINESS,
    Membership.ROLE_SUPPLIER,
    Membership.ROLE_FINANCE,
    Membership.ROLE_VIEWER,
)


def profile_type_for_membership(membership):
    if not membership:
        return None
    role = membership.role
    if role == Membership.ROLE_SUPPLIER:
        return PROFILE_SUPPLIER
    return PROFILE_INTERNAL


def default_home_for_profile(profile_type, fallback=DEFAULT_APP_HOME_PATH):
    if profile_type == PROFILE_WORKER:
        return WORKER_HOME_PATH
    return fallback


def is_frontend_path_allowed_for_profile(profile_type, path):
    parsed_path = urlparse(path or "").path
    if profile_type == PROFILE_WORKER:
        return parsed_path == WORKER_HOME_PATH
    return not (
        parsed_path == WORKER_PROFILE_ROOT
        or parsed_path.startswith(f"{WORKER_PROFILE_ROOT}/")
    )


def resolve_frontend_path_for_profile(profile_type, requested_path, fallback=DEFAULT_APP_HOME_PATH):
    default_home = default_home_for_profile(profile_type, fallback)
    candidate = requested_path or default_home
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith("/") or candidate.startswith("//"):
        return default_home
    if not is_frontend_path_allowed_for_profile(profile_type, candidate):
        return default_home
    return candidate


def resolve_frontend_path_for_membership(membership, requested_path, fallback=DEFAULT_APP_HOME_PATH):
    return resolve_frontend_path_for_profile(
        profile_type_for_membership(membership),
        requested_path,
        fallback,
    )


def build_membership_metadata(membership, fallback_home=DEFAULT_APP_HOME_PATH):
    profile_type = profile_type_for_membership(membership)
    default_home = default_home_for_profile(profile_type, fallback_home)

    return {
        "role": membership.role,
        "profile_type": profile_type,
        "profile": profile_type,
        "tenant_id": membership.tenant_id,
        "is_worker": False,
        "is_supplier": profile_type == PROFILE_SUPPLIER,
        "is_internal": profile_type == PROFILE_INTERNAL,
        "default_home": default_home,
        "home_path": default_home,
    }


def get_active_worker_profile(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return WorkerProfile.objects.filter(user=user, status=WorkerProfile.STATUS_ACTIVE).first()


def active_worker_engagements_for_profile(worker_profile):
    if not worker_profile:
        return WorkerEngagement.objects.none()
    return (
        WorkerEngagement.objects.filter(
            worker_profile=worker_profile,
            status=WorkerEngagement.STATUS_ACTIVE,
        )
        .select_related("tenant")
        .order_by("tenant__name", "id")
    )


def user_has_active_worker_engagement(user):
    worker_profile = get_active_worker_profile(user)
    if not worker_profile:
        return False
    return active_worker_engagements_for_profile(worker_profile).exists()


def serialize_worker_engagement(engagement):
    tenant = engagement.tenant
    return {
        "id": engagement.id,
        "tenant_id": engagement.tenant_id,
        "tenant_name": tenant.name if tenant else engagement.client_name,
        "client_name": engagement.client_name or (tenant.name if tenant else ""),
        "engagement_type": engagement.engagement_type,
        "status": engagement.status,
        "work_order_id": engagement.work_order_id,
        "work_order_number": engagement.work_order_number,
        "sow_id": engagement.sow_id,
        "sow_number": engagement.sow_number,
        "supplier_id": engagement.supplier_id,
        "supplier_name": engagement.supplier_name,
        "role_name": engagement.role_name,
        "start_date": engagement.start_date.isoformat() if engagement.start_date else None,
        "end_date": engagement.end_date.isoformat() if engagement.end_date else None,
        "home_path": WORKER_HOME_PATH,
    }


def get_current_worker_engagement(request, worker_profile):
    engagements = list(active_worker_engagements_for_profile(worker_profile))
    if not engagements:
        return None, []

    selected_id = None
    if hasattr(request, "session"):
        selected_id = request.session.get(SESSION_WORKER_ENGAGEMENT_ID_KEY)
    for engagement in engagements:
        if str(engagement.id) == str(selected_id):
            return engagement, engagements
    return engagements[0], engagements


def set_current_worker_engagement(request, engagement):
    if not hasattr(request, "session") or not engagement:
        return
    request.session[SESSION_WORKER_ENGAGEMENT_ID_KEY] = engagement.id
    if hasattr(request.session, "modified"):
        request.session.modified = True


def build_worker_profile_metadata():
    return {
        "type": PROFILE_WORKER,
        "profile_type": PROFILE_WORKER,
        "default_home": WORKER_HOME_PATH,
        "home_path": WORKER_HOME_PATH,
        "allowed_frontend_paths": [WORKER_HOME_PATH],
        "is_worker": True,
        "is_supplier": False,
        "is_internal": False,
    }


def build_worker_session_metadata(request, worker_profile):
    active_engagement, engagements = get_current_worker_engagement(request, worker_profile)
    if active_engagement:
        set_current_worker_engagement(request, active_engagement)

    return {
        "profile_id": worker_profile.id,
        "status": worker_profile.status,
        "active_tenant_id": active_engagement.tenant_id if active_engagement else None,
        "active_engagement_id": active_engagement.id if active_engagement else None,
        "active_engagement": (
            serialize_worker_engagement(active_engagement)
            if active_engagement
            else None
        ),
        "engagements": [serialize_worker_engagement(engagement) for engagement in engagements],
    }
