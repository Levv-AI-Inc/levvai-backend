import hashlib
import logging
import secrets
from datetime import timedelta
from html import escape
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone

from apps.accounts.membership_lifecycle import apply_membership_authorization_changes
from apps.accounts.models import Membership, SupplierContactAccess, SupplierInvite, User, WorkerProfile
from apps.accounts.password_policy import record_password_history, validate_password_policy


logger = logging.getLogger(__name__)


class SupplierContactAccessError(Exception):
    pass


def token_digest(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def generate_token():
    return f"supplier_{secrets.token_urlsafe(32)}"


def _expiry(days=7):
    return timezone.now() + timedelta(days=days)


def _latest_invitation(access):
    return access.invitations.order_by("-created_at", "-id").first()


def contact_access_payload(access):
    invitation = _latest_invitation(access)
    state = access.status
    if state == SupplierContactAccess.STATUS_PENDING and invitation and invitation.is_expired():
        state = SupplierContactAccess.STATUS_EXPIRED
    actions = []
    if state in {SupplierContactAccess.STATUS_PENDING, SupplierContactAccess.STATUS_EXPIRED}:
        actions.append("resend")
    if state in {SupplierContactAccess.STATUS_PENDING, SupplierContactAccess.STATUS_ACTIVE}:
        actions.append("revoke")
    return {
        "id": access.id,
        "supplier_id": access.supplier_id,
        "email": access.email,
        "status": state,
        "membership_id": access.membership_id,
        "delivery_status": invitation.delivery_status if invitation else "pending",
        "delivery_error": invitation.delivery_error if invitation else "",
        "expires_at": invitation.expires_at if invitation else None,
        "sent_at": invitation.sent_at if invitation else None,
        "accepted_at": access.accepted_at,
        "revoked_at": access.revoked_at,
        "allowed_actions": actions,
    }


def _registration_link(*, base_url, access, raw_token):
    query = urlencode({
        "mode": "register",
        "invite_token": raw_token,
        "email": access.email,
        "next": "/supplier/home",
    })
    return f"{base_url.rstrip('/')}/auth/login?{query}"


def _deliver(*, invite, access, raw_token, base_url):
    link = _registration_link(base_url=base_url, access=access, raw_token=raw_token)
    expires_text = timezone.localtime(invite.expires_at).strftime("%Y-%m-%d %H:%M %Z")
    subject = f"You're invited to join {access.tenant.name} on LEVV"
    text = (
        f"You have been invited to access {access.tenant.name} as a supplier contact.\n\n"
        f"Accept your single-use invitation:\n{link}\n\nThis invitation expires on {expires_text}."
    )
    html = (
        '<!doctype html><html><body style="font-family:Arial,sans-serif">'
        f"<p>You have been invited to access {escape(access.tenant.name)} as a supplier contact.</p>"
        f'<p><a href="{escape(link, quote=True)}">Accept invitation</a></p>'
        f"<p>This single-use invitation expires on {escape(expires_text)}.</p></body></html>"
    )
    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=text,
            from_email=settings.SUPPLIER_INVITE_FROM_EMAIL,
            to=[access.email],
        )
        message.attach_alternative(html, "text/html")
        message.send(fail_silently=False)
    except Exception:
        logger.exception(
            "supplier_contact_invitation_delivery_failed tenant_id=%s supplier_id=%s access_id=%s",
            access.tenant_id,
            access.supplier_id,
            access.id,
        )
        invite.delivery_status = SupplierInvite.DELIVERY_FAILED
        invite.delivery_error = "Invitation email could not be sent. Verify email settings and retry."
        invite.save(update_fields=["delivery_status", "delivery_error", "updated_at"])
        return False

    invite.delivery_status = SupplierInvite.DELIVERY_SENT
    invite.delivery_error = ""
    invite.sent_at = timezone.now()
    invite.save(update_fields=["delivery_status", "delivery_error", "sent_at", "updated_at"])
    return True


def _validate_identity_target(*, tenant, supplier_id, email):
    normalized = (email or "").strip().lower()
    user = User.objects.filter(email__iexact=normalized).first() or User.objects.filter(username__iexact=normalized).first()
    if user and WorkerProfile.objects.filter(user=user).exists():
        raise SupplierContactAccessError("Worker users cannot be invited as supplier contacts.")
    if user:
        membership = Membership.objects.filter(user=user, tenant=tenant).first()
        if membership and membership.role != Membership.ROLE_SUPPLIER:
            raise SupplierContactAccessError("This identity already has internal access in this tenant.")
        if membership and membership.supplier_id != supplier_id:
            raise SupplierContactAccessError("This supplier identity is already assigned to another supplier.")
        return user, membership
    return None, None


def _ensure_invited_membership(*, tenant, supplier_id, email):
    user, membership = _validate_identity_target(
        tenant=tenant,
        supplier_id=supplier_id,
        email=email,
    )
    if user is None:
        user = User(username=email, email=email, auth_type=User.AUTH_PASSWORD, is_active=True)
        user.set_unusable_password()
        user.save()
    if membership is None:
        membership = Membership(
            user=user,
            tenant=tenant,
            role=Membership.ROLE_SUPPLIER,
            status=Membership.STATUS_INVITED,
            is_active=True,
            supplier_id=supplier_id,
        )
        membership.full_clean()
        membership.save()
    return membership


def create_contact_invitation(*, tenant, supplier_id, email, invited_by, base_url, expires_in_days=7):
    normalized = (email or "").strip().lower()
    raw_token = generate_token()
    with transaction.atomic():
        access = SupplierContactAccess.objects.select_for_update().filter(
            tenant=tenant,
            supplier_id=supplier_id,
            email=normalized,
        ).first()
        if access and access.status in {SupplierContactAccess.STATUS_PENDING, SupplierContactAccess.STATUS_ACTIVE}:
            raise SupplierContactAccessError("Contact access already exists for this supplier and email.")
        membership = _ensure_invited_membership(
            tenant=tenant,
            supplier_id=supplier_id,
            email=normalized,
        )
        if membership.status == Membership.STATUS_ACTIVE:
            raise SupplierContactAccessError("This supplier contact already has active access.")
        apply_membership_authorization_changes(membership, status=Membership.STATUS_INVITED, supplier_id=supplier_id)
        membership.full_clean()
        membership.save()
        if access is None:
            access = SupplierContactAccess(
                tenant=tenant,
                supplier_id=supplier_id,
                email=normalized,
                membership=membership,
                invited_by=invited_by,
            )
        else:
            access.membership = membership
            access.status = SupplierContactAccess.STATUS_PENDING
            access.invited_by = invited_by
            access.accepted_at = None
            access.revoked_at = None
        access.full_clean()
        access.save()
        # A revived expired/revoked access target must never leave a second
        # pending credential behind, including a legacy plaintext token.
        SupplierInvite.objects.filter(
            contact_access=access,
            status=SupplierInvite.STATUS_PENDING,
        ).update(
            status=SupplierInvite.STATUS_REVOKED,
            revoked_at=timezone.now(),
            token_hash=None,
            token=None,
        )
        invite = SupplierInvite.objects.create(
            contact_access=access,
            tenant=tenant,
            supplier_id=supplier_id,
            email=normalized,
            token_hash=token_digest(raw_token),
            token_hint=raw_token[-8:],
            invited_by=invited_by,
            expires_at=_expiry(expires_in_days),
        )
    _deliver(invite=invite, access=access, raw_token=raw_token, base_url=base_url)
    return access


def resend_contact_invitation(*, tenant, supplier_id, access_id, invited_by, base_url, expires_in_days=7):
    raw_token = generate_token()
    with transaction.atomic():
        access = SupplierContactAccess.objects.select_for_update().select_related("membership__user", "tenant").filter(
            id=access_id, tenant=tenant, supplier_id=supplier_id
        ).first()
        if not access:
            raise SupplierContactAccessError("Supplier contact access was not found.")
        if access.status == SupplierContactAccess.STATUS_ACTIVE:
            raise SupplierContactAccessError("Active supplier access cannot be resent.")
        if access.status == SupplierContactAccess.STATUS_REVOKED:
            raise SupplierContactAccessError("Revoked supplier access must be invited again.")
        membership = access.membership
        if not membership:
            membership = _ensure_invited_membership(
                tenant=tenant, supplier_id=supplier_id, email=access.email
            )
            access.membership = membership
        apply_membership_authorization_changes(membership, status=Membership.STATUS_INVITED, supplier_id=supplier_id)
        membership.full_clean()
        membership.save()
        SupplierInvite.objects.filter(contact_access=access, status=SupplierInvite.STATUS_PENDING).update(
            status=SupplierInvite.STATUS_REVOKED,
            revoked_at=timezone.now(),
            token_hash=None,
            token=None,
        )
        access.status = SupplierContactAccess.STATUS_PENDING
        access.invited_by = invited_by
        access.accepted_at = None
        access.revoked_at = None
        access.save()
        invite = SupplierInvite.objects.create(
            contact_access=access,
            tenant=tenant,
            supplier_id=supplier_id,
            email=access.email,
            token_hash=token_digest(raw_token),
            token_hint=raw_token[-8:],
            invited_by=invited_by,
            expires_at=_expiry(expires_in_days),
        )
    _deliver(invite=invite, access=access, raw_token=raw_token, base_url=base_url)
    return access


def revoke_contact_access(*, tenant, supplier_id, access_id):
    with transaction.atomic():
        access = SupplierContactAccess.objects.select_for_update().select_related("membership__user").filter(
            id=access_id, tenant=tenant, supplier_id=supplier_id
        ).first()
        if not access:
            raise SupplierContactAccessError("Supplier contact access was not found.")
        if access.status == SupplierContactAccess.STATUS_REVOKED:
            return access
        now = timezone.now()
        SupplierInvite.objects.filter(contact_access=access, status=SupplierInvite.STATUS_PENDING).update(
            status=SupplierInvite.STATUS_REVOKED,
            revoked_at=now,
            token_hash=None,
            token=None,
        )
        membership = access.membership
        if membership:
            if (
                membership.tenant_id != tenant.id
                or membership.role != Membership.ROLE_SUPPLIER
                or membership.supplier_id != supplier_id
                or (membership.user.email or "").strip().lower() != access.email
            ):
                raise SupplierContactAccessError("Contact access membership no longer matches the reviewed target.")
            apply_membership_authorization_changes(membership, status=Membership.STATUS_DEACTIVATED)
            membership.full_clean()
            membership.save()
        access.status = SupplierContactAccess.STATUS_REVOKED
        access.revoked_at = now
        access.save(update_fields=["status", "revoked_at", "updated_at"])
        return access


def _find_locked_invitation(token):
    digest = token_digest(token)
    invitation = SupplierInvite.objects.select_for_update().select_related(
        "contact_access__membership__user", "contact_access__tenant", "tenant"
    ).filter(token_hash=digest).first()
    if invitation:
        return invitation
    # Deliberate one-time compatibility for pending credentials created before
    # hashed supplier invitations. Resend/revoke clears this legacy token.
    return SupplierInvite.objects.select_for_update().select_related(
        "contact_access__membership__user", "contact_access__tenant", "tenant"
    ).filter(token=token, token_hash__isnull=True).first()


def accept_contact_invitation(*, tenant, token, email, password, first_name="", last_name=""):
    normalized = (email or "").strip().lower()
    with transaction.atomic():
        invitation = _find_locked_invitation(token)
        if not invitation:
            raise SupplierContactAccessError("Invite is invalid.")
        if invitation.tenant_id != tenant.id:
            raise SupplierContactAccessError("Invite does not belong to this tenant.")
        if invitation.is_expired():
            invitation.status = SupplierInvite.STATUS_EXPIRED
            invitation.save(update_fields=["status", "updated_at"])
            if invitation.contact_access_id:
                SupplierContactAccess.objects.filter(id=invitation.contact_access_id).update(
                    status=SupplierContactAccess.STATUS_EXPIRED
                )
            raise SupplierContactAccessError("Invite has expired.")
        if invitation.status != SupplierInvite.STATUS_PENDING:
            raise SupplierContactAccessError("Invite is no longer active.")
        if invitation.email.strip().lower() != normalized:
            raise SupplierContactAccessError("Invite email does not match.")
        access = invitation.contact_access
        if not access:
            access, _ = SupplierContactAccess.objects.select_for_update().get_or_create(
                tenant=tenant,
                supplier_id=invitation.supplier_id,
                email=normalized,
                defaults={"invited_by": invitation.invited_by},
            )
            invitation.contact_access = access
            invitation.save(update_fields=["contact_access", "updated_at"])
        if access.tenant_id != tenant.id or access.supplier_id != invitation.supplier_id or access.email != normalized:
            raise SupplierContactAccessError("Invite does not match the reviewed supplier access.")
        if access.status != SupplierContactAccess.STATUS_PENDING:
            raise SupplierContactAccessError("Supplier contact access is no longer pending.")

        membership = access.membership
        if not membership:
            membership = _ensure_invited_membership(
                tenant=tenant, supplier_id=access.supplier_id, email=normalized
            )
            access.membership = membership
        if (
            membership.tenant_id != tenant.id
            or membership.role != Membership.ROLE_SUPPLIER
            or membership.supplier_id != access.supplier_id
            or (membership.user.email or "").strip().lower() != normalized
        ):
            raise SupplierContactAccessError("The invited membership no longer matches this supplier access.")
        if membership.status != Membership.STATUS_INVITED:
            raise SupplierContactAccessError("The invited membership is no longer available.")
        user = membership.user
        linked_existing_user = user.has_usable_password()
        if linked_existing_user:
            if not user.check_password(password):
                raise SupplierContactAccessError("Existing user password is incorrect for this email.")
        else:
            try:
                validate_password_policy(password, tenant, user=user)
            except ValidationError as exc:
                messages = list(getattr(exc, "messages", []) or [])
                raise SupplierContactAccessError(messages or ["Password does not meet policy requirements."]) from exc
            user.set_password(password)
            if first_name:
                user.first_name = first_name
            if last_name:
                user.last_name = last_name
            user.save()
            record_password_history(user, tenant)

        apply_membership_authorization_changes(membership, status=Membership.STATUS_ACTIVE)
        membership.full_clean()
        membership.save()
        now = timezone.now()
        access.status = SupplierContactAccess.STATUS_ACTIVE
        access.accepted_at = now
        access.revoked_at = None
        access.save(update_fields=["membership", "status", "accepted_at", "revoked_at", "updated_at"])
        invitation.status = SupplierInvite.STATUS_ACCEPTED
        invitation.accepted_at = now
        invitation.accepted_by = user
        invitation.token_hash = None
        invitation.token = None
        invitation.save(update_fields=["status", "accepted_at", "accepted_by", "token_hash", "token", "updated_at"])
        return access, membership, linked_existing_user
