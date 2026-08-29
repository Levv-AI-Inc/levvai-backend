from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import WorkerEngagement, WorkerProfile


def ensure_worker_engagement_for_work_order(*, tenant, work_order, invited_by=None):
    email = (work_order.worker_email or "").strip().lower()
    if not email:
        return None

    full_name = (work_order.worker_full_name or "").strip()
    first_name, last_name = _split_name(full_name)
    User = get_user_model()

    with transaction.atomic():
        user = (
            User.objects.filter(email__iexact=email)
            .order_by("id")
            .first()
        )
        if not user:
            user = User(
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
            "status": WorkerEngagement.STATUS_ACTIVE,
            "activated_at": timezone.now(),
        }
        if invited_by:
            defaults["invited_by"] = invited_by

        engagement, _ = WorkerEngagement.objects.update_or_create(
            worker_profile=worker_profile,
            tenant=tenant,
            engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
            work_order_id=work_order.id,
            defaults=defaults,
        )
        engagement.full_clean()
        return engagement


def _split_name(full_name):
    parts = [part for part in full_name.split(" ") if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])
