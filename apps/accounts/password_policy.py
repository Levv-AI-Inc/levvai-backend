from types import SimpleNamespace
from datetime import timedelta

from django.contrib.auth.hashers import check_password
from django.contrib.auth.password_validation import CommonPasswordValidator
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import LoginAttempt, PasswordHistory, PasswordPolicy


DEFAULT_POLICY = {
    "min_length": 12,
    "min_character_sets": 3,
    "history_count": 5,
    "max_failed_attempts": 5,
    "lockout_minutes": 15,
    "block_common_passwords": True,
    "expiration_days": None,
}


def get_password_policy():
    policy = PasswordPolicy.objects.order_by("id").first()
    if policy:
        return policy
    return SimpleNamespace(**DEFAULT_POLICY)


def _character_set_count(password):
    has_upper = any(ch.isupper() for ch in password)
    has_lower = any(ch.islower() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    has_special = any(not ch.isalnum() for ch in password)
    return sum([has_upper, has_lower, has_digit, has_special])


def validate_password_policy(password, tenant, user=None):
    policy = get_password_policy()
    errors = []

    if len(password) < policy.min_length:
        errors.append(f"Password must be at least {policy.min_length} characters.")

    if _character_set_count(password) < policy.min_character_sets:
        errors.append("Password must include at least three of: uppercase, lowercase, number, special character.")

    if policy.block_common_passwords:
        try:
            CommonPasswordValidator().validate(password)
        except ValidationError as exc:
            errors.extend(exc.messages)

    if user and policy.history_count:
        recent = PasswordHistory.objects.filter(user=user, tenant=tenant).order_by("-created_at")[: policy.history_count]
        for entry in recent:
            if check_password(password, entry.password_hash):
                errors.append(f"Password cannot reuse the last {policy.history_count} passwords.")
                break

    if errors:
        raise ValidationError(errors)


def record_password_history(user, tenant, policy=None):
    policy = policy or get_password_policy()
    PasswordHistory.objects.create(user=user, tenant=tenant, password_hash=user.password)

    if policy.history_count:
        qs = PasswordHistory.objects.filter(user=user, tenant=tenant).order_by("-created_at")
        ids = list(qs.values_list("id", flat=True)[policy.history_count :])
        if ids:
            PasswordHistory.objects.filter(id__in=ids).delete()


def password_is_expired(user, tenant, policy=None):
    policy = policy or get_password_policy()
    if not policy.expiration_days:
        return False
    last = PasswordHistory.objects.filter(user=user, tenant=tenant).order_by("-created_at").first()
    if not last:
        return False
    return last.created_at + timedelta(days=policy.expiration_days) < timezone.now()


def get_login_attempt(user, tenant):
    attempt, _ = LoginAttempt.objects.get_or_create(user=user, tenant=tenant)
    return attempt


def register_failed_login(user, tenant, policy=None):
    policy = policy or get_password_policy()
    attempt = get_login_attempt(user, tenant)
    attempt.failed_count += 1
    if attempt.failed_count >= policy.max_failed_attempts:
        attempt.locked_until = timezone.now() + timedelta(minutes=policy.lockout_minutes)
    attempt.save(update_fields=["failed_count", "locked_until", "updated_at"])
    return attempt


def register_successful_login(user, tenant):
    attempt = get_login_attempt(user, tenant)
    if attempt.failed_count or attempt.locked_until:
        attempt.failed_count = 0
        attempt.locked_until = None
        attempt.save(update_fields=["failed_count", "locked_until", "updated_at"])
    return attempt
