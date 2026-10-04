from django.core.exceptions import ValidationError

from apps.accounts.models import Membership


_UNSET = object()


def normalize_membership_status(value):
    status = (value or "").strip().lower()
    if status == Membership.STATUS_DISABLED:
        return Membership.STATUS_SUSPENDED
    return status


def status_keeps_legacy_membership_active(status):
    """Keep the legacy boolean synchronized without changing invited behavior."""
    return status not in {
        Membership.STATUS_SUSPENDED,
        Membership.STATUS_DEACTIVATED,
    }


def validate_final_active_administrator(membership, *, new_role, new_status):
    currently_active_admin = (
        membership.role == Membership.ROLE_ADMIN
        and membership.status == Membership.STATUS_ACTIVE
        and membership.is_active
    )
    remains_active_admin = (
        new_role == Membership.ROLE_ADMIN
        and new_status == Membership.STATUS_ACTIVE
    )
    if not currently_active_admin or remains_active_admin:
        return

    another_active_admin_exists = (
        Membership.objects.filter(
            tenant_id=membership.tenant_id,
            role=Membership.ROLE_ADMIN,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        )
        .exclude(id=membership.id)
        .exists()
    )
    if not another_active_admin_exists:
        raise ValidationError(
            {"status": "The final active customer administrator cannot be changed or disabled."}
        )


def apply_membership_authorization_changes(
    membership,
    *,
    role=_UNSET,
    status=_UNSET,
    business_unit_id=_UNSET,
    supplier_id=_UNSET,
):
    """Apply one authorization mutation and advance its session version once."""
    changed = False

    if role is not _UNSET and membership.role != role:
        membership.role = role
        changed = True

    if status is not _UNSET:
        normalized_status = normalize_membership_status(status)
        next_is_active = status_keeps_legacy_membership_active(normalized_status)
        if membership.status != normalized_status or membership.is_active != next_is_active:
            membership.status = normalized_status
            membership.is_active = next_is_active
            changed = True

    if business_unit_id is not _UNSET and membership.business_unit_id != business_unit_id:
        membership.business_unit_id = business_unit_id
        changed = True

    if supplier_id is not _UNSET and membership.supplier_id != supplier_id:
        membership.supplier_id = supplier_id
        changed = True

    if changed:
        membership.authorization_version = (membership.authorization_version or 0) + 1

    return changed
