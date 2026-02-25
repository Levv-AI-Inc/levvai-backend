from django.contrib import admin
from django.urls import include, path

from apps.common.views import healthz
from apps.tenants.api import TenantCreateView
from apps.tenants.views import provision_domain_task
from apps.accounts.api import SupplierPasswordLoginView, SupplierRegisterView, UserPasswordLoginView, UserRegisterView, WorkOSCallbackView, WorkOSLoginView

urlpatterns = [
    path("healthz", healthz),
    path("admin/tenants", TenantCreateView.as_view(), name="tenants-admin-create"),
    path("admin/", include("apps.masterdata.urls")),
    path("django-admin/", admin.site.urls),
    path("auth/", include("dj_rest_auth.urls")),
    path("auth/registration/", include("dj_rest_auth.registration.urls")),
    path("auth/password/register", SupplierRegisterView.as_view(), name="supplier-register"),
    path("auth/password/login", SupplierPasswordLoginView.as_view(), name="supplier-login"),
    path("auth/password/register-user", UserRegisterView.as_view(), name="user-register"),
    path("auth/password/login-user", UserPasswordLoginView.as_view(), name="user-login"),
    path("auth/workos/login", WorkOSLoginView.as_view(), name="workos-login"),
    path("auth/workos/callback", WorkOSCallbackView.as_view(), name="workos-callback"),
    path("tasks/provision-domain", provision_domain_task),
]
