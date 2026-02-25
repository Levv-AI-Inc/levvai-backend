import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from project root into process env vars.
_dotenv_path = BASE_DIR / ".env"
# Load .env if present (local dev), otherwise rely on injected env vars (e.g., Cloud Run).
load_dotenv(_dotenv_path)


def env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and value in (None, ""):
        raise RuntimeError(f"Missing required env var: {name}")
    return value


SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
DEBUG = env("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

PUBLIC_SCHEMA_NAME = "public"

SHARED_APPS = [
    "django_tenants",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sites",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "dj_rest_auth",
    "dj_rest_auth.registration",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "apps.tenants",
    "apps.accounts",
]

TENANT_APPS = [
    "apps.audit",
    "apps.masterdata",
    "apps.policies",
]

INSTALLED_APPS = SHARED_APPS + TENANT_APPS

MIDDLEWARE = [
    "apps.tenants.middleware.TenantExistenceMiddleware",
    "django_tenants.middleware.TenantMainMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "apps.common.middleware.TenantContextMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.common.middleware.TenantMembershipMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "levvai.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "levvai.wsgi.application"
ASGI_APPLICATION = "levvai.asgi.application"

_db_config = dj_database_url.parse(env("DATABASE_URL", required=True), conn_max_age=600)
# Required for django-tenants to add schema_name support.
_db_config["ENGINE"] = "django_tenants.postgresql_backend"
DATABASES = {"default": _db_config}

DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]

TENANT_MODEL = "tenants.Tenant"
TENANT_DOMAIN_MODEL = "tenants.Domain"

AUTH_USER_MODEL = "accounts.User"
SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = env("DJANGO_STATIC_ROOT", str(BASE_DIR / "staticfiles"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

REST_AUTH = {
    "SESSION_LOGIN": True,
    "USE_JWT": False,
    # Disable token auth model since we're using session auth only.
    "TOKEN_MODEL": None,
}

ACCOUNT_LOGIN_METHODS = {"email", "username"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "username*", "password1*", "password2*"]

WORKOS_API_KEY = env("WORKOS_API_KEY")
WORKOS_CLIENT_ID = env("WORKOS_CLIENT_ID")
WORKOS_DEFAULT_NEXT_URL = env("WORKOS_DEFAULT_NEXT_URL", "/django-admin/")
WORKOS_DEFAULT_ROLE = env("WORKOS_DEFAULT_ROLE", "business")
PASSWORD_DEFAULT_ROLE = env("PASSWORD_DEFAULT_ROLE", "business")

CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "tenant_context": {
            "()": "apps.common.logging.TenantContextFilter",
        }
    },
    "formatters": {
        "structured": {
            "format": "{levelname} {asctime} tenant_id={tenant_id} {name} {message}",
            "style": "{",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["tenant_context"],
            "formatter": "structured",
        }
    },
    "root": {
        "handlers": ["console"],
        "level": env("DJANGO_LOG_LEVEL", "INFO"),
    },
}
