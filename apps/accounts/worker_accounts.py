from html import escape
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User, WorkerEngagement, WorkerInvite, WorkerProfile
from apps.accounts.password_policy import record_password_history, validate_password_policy
from apps.accounts.profile import WORKER_HOME_PATH


class WorkerInviteDeliveryError(Exception):
    pass


class WorkerInviteValidationError(Exception):
    pass


def ensure_worker_engagement_for_work_order(*, tenant, work_order, invited_by=None, activate=True):
    email = (work_order.worker_email or "").strip().lower()
    if not email:
        return None

    full_name = (work_order.worker_full_name or "").strip()
    first_name, last_name = _split_name(full_name)
    UserModel = get_user_model()

    with transaction.atomic():
        user = (
            UserModel.objects.filter(Q(email__iexact=email) | Q(username__iexact=email))
            .order_by("id")
            .first()
        )
        if not user:
            user = UserModel(
                username=email,
                email=email,
                first_name=first_name,
                last_name=last_name,
                is_active=True,
            )
            user.set_unusable_password()
            user.save()
        else:
            updates = []
            if not user.email:
                user.email = email
                updates.append("email")
            if first_name and not user.first_name:
                user.first_name = first_name
                updates.append("first_name")
            if last_name and not user.last_name:
                user.last_name = last_name
                updates.append("last_name")
            if updates:
                user.save(update_fields=updates)

        worker_profile, _ = WorkerProfile.objects.get_or_create(
            user=user,
            defaults={
                "status": WorkerProfile.STATUS_ACTIVE,
                "preferred_name": full_name,
                "phone": work_order.worker_phone,
            },
        )
        profile_updates = []
        if worker_profile.status != WorkerProfile.STATUS_ACTIVE:
            worker_profile.status = WorkerProfile.STATUS_ACTIVE
            profile_updates.append("status")
        if full_name and not worker_profile.preferred_name:
            worker_profile.preferred_name = full_name
            profile_updates.append("preferred_name")
        if work_order.worker_phone and not worker_profile.phone:
            worker_profile.phone = work_order.worker_phone
            profile_updates.append("phone")
        if profile_updates:
            worker_profile.full_clean()
            worker_profile.save(update_fields=[*profile_updates, "updated_at"])

        defaults = {
            "work_order_number": work_order.work_order_number or "",
            "supplier_id": work_order.supplier_id,
            "supplier_name": work_order.supplier.name if work_order.supplier else "",
            "client_name": tenant.name if tenant else "",
            "role_name": work_order.role_definition.name if work_order.role_definition else "",
            "start_date": work_order.start_date,
            "end_date": work_order.end_date,
            "status": WorkerEngagement.STATUS_ACTIVE if activate else WorkerEngagement.STATUS_INVITED,
            "activated_at": timezone.now() if activate else None,
        }
        if invited_by:
            defaults["invited_by"] = invited_by

        engagement, created = WorkerEngagement.objects.get_or_create(
            worker_profile=worker_profile,
            tenant=tenant,
            engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
            work_order_id=work_order.id,
            defaults=defaults,
        )
        if not created:
            changed_fields = []
            for field, value in defaults.items():
                if field in {"status", "activated_at"}:
                    continue
                if getattr(engagement, field) != value:
                    setattr(engagement, field, value)
                    changed_fields.append(field)
            if activate and engagement.status != WorkerEngagement.STATUS_ACTIVE:
                engagement.status = WorkerEngagement.STATUS_ACTIVE
                engagement.activated_at = timezone.now()
                changed_fields.extend(["status", "activated_at"])
            if changed_fields:
                engagement.full_clean()
                engagement.save(update_fields=[*changed_fields, "updated_at"])

        engagement.full_clean()
        return engagement


def activate_worker_engagement(engagement):
    if engagement.status != WorkerEngagement.STATUS_ACTIVE:
        engagement.status = WorkerEngagement.STATUS_ACTIVE
        engagement.activated_at = timezone.now()
        engagement.full_clean()
        engagement.save(update_fields=["status", "activated_at", "updated_at"])
    return engagement


def issue_worker_invite(*, tenant, work_order, worker_profile, invited_by, base_url, send_email=True):
    user = worker_profile.user
    if user.auth_type == User.AUTH_SSO or user.has_usable_password():
        return None, ""

    now = timezone.now()
    WorkerInvite.objects.filter(
        worker_profile=worker_profile,
        status=WorkerInvite.STATUS_PENDING,
        expires_at__lte=now,
    ).update(status=WorkerInvite.STATUS_EXPIRED, updated_at=now)

    invite = (
        WorkerInvite.objects.filter(
            worker_profile=worker_profile,
            tenant=tenant,
            work_order_id=work_order.id,
            status=WorkerInvite.STATUS_PENDING,
            expires_at__gt=now,
        )
        .order_by("-created_at")
        .first()
    )
    if invite is None:
        invite = WorkerInvite(
            worker_profile=worker_profile,
            tenant=tenant,
            work_order_id=work_order.id,
            email=user.email,
            invited_by=invited_by,
        )
        invite.full_clean()
        invite.save()

    registration_link = build_worker_registration_link(base_url=base_url, invite=invite)
    if send_email:
        send_worker_invite_email(invite=invite, registration_link=registration_link)
    return invite, registration_link


def build_worker_registration_link(*, base_url, invite):
    query = urlencode(
        {
            "mode": "register",
            "invite_token": invite.token,
            "email": invite.email,
            "next": WORKER_HOME_PATH,
        }
    )
    return f"{base_url.rstrip('/')}/auth/login?{query}"


def send_worker_invite_email(*, invite, registration_link):
    expires_text = timezone.localtime(invite.expires_at).strftime("%Y-%m-%d %H:%M %Z")
    worker_name = invite.worker_profile.preferred_name or invite.worker_profile.user.get_full_name().strip() or "there"
    subject = "Complete your worker registration on LEVV"
    text_body = (
        f"Hi {worker_name},\n\n"
        "Your work order has been accepted and your onboarding is ready.\n"
        f"Create your account using this link:\n{registration_link}\n\n"
        f"This invite expires on {expires_text}."
    )
    link_safe = escape(registration_link, quote=True)
    html_body = (
        '<!doctype html><html><body style="font-family:Arial,sans-serif;color:#0f172a;'
        'background:#f4f7fb;padding:32px"><div style="max-width:620px;margin:auto;background:#fff;'
        'border:1px solid #e2e8f0;padding:28px">'
        f'<h2 style="margin-top:0">Welcome to LEVV, {escape(worker_name)}</h2>'
        "<p>Your work order has been accepted and your onboarding is ready.</p>"
        f'<p><a href="{link_safe}" style="display:inline-block;background:#020617;color:#fff;'
        'text-decoration:none;padding:12px 18px;border-radius:6px">Complete registration</a></p>'
        f'<p style="font-size:12px;color:#64748b">Expires {escape(expires_text)}</p>'
        "</div></body></html>"
    )
    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.WORKER_INVITE_FROM_EMAIL,
            to=[invite.email],
        )
        message.attach_alternative(html_body, "text/html")
        message.send(fail_silently=False)
    except Exception as exc:
        raise WorkerInviteDeliveryError("Worker registration email could not be sent.") from exc


def register_worker_invite(*, tenant, email, password, token):
    email = (email or "").strip().lower()

    with transaction.atomic():
        invite = (
            WorkerInvite.objects.select_for_update()
            .select_related("worker_profile__user", "tenant")
            .filter(token=token)
            .first()
        )
        if not invite:
            raise WorkerInviteValidationError("Invite is invalid.")
        if invite.tenant_id != tenant.id:
            raise WorkerInviteValidationError("Invite does not belong to this tenant.")
        if invite.is_expired():
            invite.mark_expired()
            raise WorkerInviteValidationError("Invite has expired.")
        if invite.status != WorkerInvite.STATUS_PENDING:
            raise WorkerInviteValidationError("Invite is no longer active.")
        if invite.email.strip().lower() != email:
            raise WorkerInviteValidationError("Invite email does not match.")

        worker_profile = invite.worker_profile
        user = worker_profile.user
        if user.auth_type == User.AUTH_SSO:
            raise WorkerInviteValidationError("SSO users cannot use password signup.")
        if user.has_usable_password():
            raise WorkerInviteValidationError("Worker is already registered.")

        try:
            validate_password_policy(password, tenant, user=user)
        except ValidationError as exc:
            messages = list(getattr(exc, "messages", []) or [])
            raise WorkerInviteValidationError(
                messages or ["Password does not meet policy requirements."]
            ) from exc

        engagement = (
            WorkerEngagement.objects.select_for_update()
            .filter(
                worker_profile=worker_profile,
                tenant=tenant,
                engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
                work_order_id=invite.work_order_id,
            )
            .first()
        )
        if not engagement:
            raise WorkerInviteValidationError("The worker engagement no longer exists.")

        user.set_password(password)
        user.auth_type = User.AUTH_PASSWORD
        user.is_active = True
        user.save(update_fields=["password", "auth_type", "is_active"])
        record_password_history(user, tenant)

        if worker_profile.status != WorkerProfile.STATUS_ACTIVE:
            worker_profile.status = WorkerProfile.STATUS_ACTIVE
            worker_profile.save(update_fields=["status", "updated_at"])
        activate_worker_engagement(engagement)
        activated_at = timezone.now()
        WorkerEngagement.objects.filter(
            worker_profile=worker_profile,
            status=WorkerEngagement.STATUS_INVITED,
        ).exclude(pk=engagement.pk).update(
            status=WorkerEngagement.STATUS_ACTIVE,
            activated_at=activated_at,
            updated_at=activated_at,
        )
        invite.mark_accepted(user=user)
        WorkerInvite.objects.filter(
            worker_profile=worker_profile,
            status=WorkerInvite.STATUS_PENDING,
        ).exclude(pk=invite.pk).update(status=WorkerInvite.STATUS_REVOKED, updated_at=timezone.now())

    return user, worker_profile, engagement


def _split_name(full_name):
    parts = [part for part in full_name.split(" ") if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])
