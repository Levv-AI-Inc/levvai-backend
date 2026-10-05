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
from apps.accounts.models import Membership, User, UserInvitation
from apps.accounts.password_policy import record_password_history, validate_password_policy


logger = logging.getLogger(__name__)


class UserInvitationValidationError(Exception):
    pass


def token_digest(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def generate_token():
    return f"user_{secrets.token_urlsafe(32)}"


def invitation_expiry():
    return timezone.now() + timedelta(days=settings.USER_INVITE_EXPIRY_DAYS)


def invitation_payload(invitation):
    return {
        "id": invitation.id,
        "status": invitation.status,
        "delivery_status": invitation.delivery_status,
        "delivery_error": invitation.delivery_error,
        "expires_at": invitation.expires_at,
        "sent_at": invitation.sent_at,
    }


def build_registration_link(*, base_url, invitation, raw_token):
    query = urlencode(
        {
            "mode": "register",
            "invitation_type": "user",
            "invite_token": raw_token,
            "email": invitation.email,
            "auth": invitation.membership.user.auth_type,
            "existing_password": (
                "1"
                if invitation.membership.user.auth_type == User.AUTH_PASSWORD
                and invitation.membership.user.has_usable_password()
                else "0"
            ),
            "next": "/home",
        }
    )
    return f"{base_url.rstrip('/')}/auth/login?{query}"


def _email_content(*, invitation, registration_link):
    tenant_name = invitation.tenant.name
    invited_name = invitation.membership.user.get_full_name().strip() or "there"
    expires_text = timezone.localtime(invitation.expires_at).strftime("%Y-%m-%d %H:%M %Z")
    auth_copy = (
        "Accept the invitation, then continue with your organization's SSO."
        if invitation.membership.user.auth_type == User.AUTH_SSO
        else "Accept the invitation and secure your account with your password."
    )
    subject = f"You're invited to join {tenant_name} on LEVV"
    text_body = (
        f"Hi {invited_name},\n\n"
        f"You have been invited to join {tenant_name} on LEVV. {auth_copy}\n"
        f"Accept your invitation using this link:\n{registration_link}\n\n"
        f"This single-use invitation expires on {expires_text}."
    )
    link_safe = escape(registration_link, quote=True)
    html_body = (
        '<!doctype html><html><body style="font-family:Arial,sans-serif;color:#0f172a;'
        'background:#f4f7fb;padding:32px"><div style="max-width:620px;margin:auto;background:#fff;'
        'border:1px solid #e2e8f0;padding:28px">'
        f'<h2 style="margin-top:0">Welcome to LEVV, {escape(invited_name)}</h2>'
        f"<p>You have been invited to join {escape(tenant_name)} on LEVV.</p>"
        f"<p>{escape(auth_copy)}</p>"
        f'<p><a href="{link_safe}" style="display:inline-block;background:#020617;color:#fff;'
        'text-decoration:none;padding:12px 18px;border-radius:6px">Accept invitation</a></p>'
        f'<p style="font-size:12px;color:#64748b">Expires {escape(expires_text)}</p>'
        "</div></body></html>"
    )
    return subject, text_body, html_body


def deliver_invitation(*, invitation, raw_token, base_url):
    registration_link = build_registration_link(
        base_url=base_url,
        invitation=invitation,
        raw_token=raw_token,
    )
    subject, text_body, html_body = _email_content(
        invitation=invitation,
        registration_link=registration_link,
    )
    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.USER_INVITE_FROM_EMAIL,
            to=[invitation.email],
        )
        message.attach_alternative(html_body, "text/html")
        message.send(fail_silently=False)
    except Exception:
        logger.exception(
            "user_invitation_delivery_failed tenant_id=%s membership_id=%s invitation_id=%s",
            invitation.tenant_id,
            invitation.membership_id,
            invitation.id,
        )
        invitation.delivery_status = UserInvitation.DELIVERY_FAILED
        invitation.delivery_error = "Invitation email could not be sent. Verify email settings and retry."
        invitation.save(update_fields=["delivery_status", "delivery_error", "updated_at"])
        return False

    invitation.delivery_status = UserInvitation.DELIVERY_SENT
    invitation.delivery_error = ""
    invitation.sent_at = timezone.now()
    invitation.save(update_fields=["delivery_status", "delivery_error", "sent_at", "updated_at"])
    return True


def create_invitation(*, membership, invited_by, base_url):
    raw_token = generate_token()
    invitation = UserInvitation(
        membership=membership,
        tenant=membership.tenant,
        email=membership.user.email,
        token_hash=token_digest(raw_token),
        token_hint=raw_token[-8:],
        invited_by=invited_by,
        expires_at=invitation_expiry(),
    )
    invitation.full_clean()
    invitation.save()
    deliver_invitation(invitation=invitation, raw_token=raw_token, base_url=base_url)
    return invitation


def resend_invitation(*, invitation_id, tenant, invited_by, base_url):
    raw_token = generate_token()
    with transaction.atomic():
        invitation = (
            UserInvitation.objects.select_for_update()
            .select_related("membership__user", "tenant")
            .filter(id=invitation_id, tenant=tenant)
            .first()
        )
        if not invitation:
            raise UserInvitationValidationError("Invitation was not found.")
        if invitation.status == UserInvitation.STATUS_ACCEPTED:
            raise UserInvitationValidationError("Accepted invitations cannot be resent.")
        if invitation.membership.status != Membership.STATUS_INVITED:
            raise UserInvitationValidationError("Only invited memberships can be resent.")

        invitation.token_hash = token_digest(raw_token)
        invitation.token_hint = raw_token[-8:]
        invitation.status = UserInvitation.STATUS_PENDING
        invitation.delivery_status = UserInvitation.DELIVERY_PENDING
        invitation.delivery_error = ""
        invitation.invited_by = invited_by
        invitation.expires_at = invitation_expiry()
        invitation.sent_at = None
        invitation.accepted_at = None
        invitation.accepted_by = None
        invitation.revoked_at = None
        invitation.full_clean()
        invitation.save()

    deliver_invitation(invitation=invitation, raw_token=raw_token, base_url=base_url)
    return invitation


def revoke_invitation(*, invitation_id, tenant):
    with transaction.atomic():
        invitation = (
            UserInvitation.objects.select_for_update()
            .select_related("membership__user", "tenant")
            .filter(id=invitation_id, tenant=tenant)
            .first()
        )
        if not invitation:
            raise UserInvitationValidationError("Invitation was not found.")
        if invitation.status == UserInvitation.STATUS_ACCEPTED:
            raise UserInvitationValidationError("Accepted invitations cannot be revoked.")
        invitation.status = UserInvitation.STATUS_REVOKED
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["status", "revoked_at", "updated_at"])
        return invitation


def accept_invitation(*, tenant, token, email, password=""):
    digest = token_digest(token)
    normalized_email = (email or "").strip().lower()

    with transaction.atomic():
        invitation = (
            UserInvitation.objects.select_for_update()
            .select_related("membership__user", "tenant")
            .filter(token_hash=digest)
            .first()
        )
        if not invitation:
            raise UserInvitationValidationError("Invitation is invalid.")
        if invitation.tenant_id != tenant.id:
            raise UserInvitationValidationError("Invitation does not belong to this tenant.")
        if invitation.is_expired():
            invitation.status = UserInvitation.STATUS_EXPIRED
            invitation.save(update_fields=["status", "updated_at"])
            raise UserInvitationValidationError("Invitation has expired.")
        if invitation.status != UserInvitation.STATUS_PENDING:
            raise UserInvitationValidationError("Invitation is no longer active.")
        if invitation.email != normalized_email:
            raise UserInvitationValidationError("Invitation email does not match.")

        membership = Membership.objects.select_for_update().get(id=invitation.membership_id)
        user = membership.user
        if membership.tenant_id != tenant.id or membership.status != Membership.STATUS_INVITED:
            raise UserInvitationValidationError("The invited membership is no longer available.")

        linked_existing_user = user.has_usable_password()
        if user.auth_type == User.AUTH_PASSWORD:
            if not password:
                raise UserInvitationValidationError("Password is required.")
            if linked_existing_user:
                if not user.check_password(password):
                    raise UserInvitationValidationError("Existing user password is incorrect for this email.")
            else:
                try:
                    validate_password_policy(password, tenant, user=user)
                except ValidationError as exc:
                    messages = list(getattr(exc, "messages", []) or [])
                    raise UserInvitationValidationError(
                        messages or ["Password does not meet policy requirements."]
                    ) from exc
                user.set_password(password)
                user.save(update_fields=["password"])
                record_password_history(user, tenant)

        apply_membership_authorization_changes(
            membership,
            status=Membership.STATUS_ACTIVE,
        )
        membership.full_clean()
        membership.save()

        invitation.status = UserInvitation.STATUS_ACCEPTED
        invitation.accepted_at = timezone.now()
        invitation.accepted_by = user
        invitation.save(update_fields=["status", "accepted_at", "accepted_by", "updated_at"])

    return invitation, membership, linked_existing_user
