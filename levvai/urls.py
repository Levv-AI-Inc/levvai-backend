from django.contrib import admin
from django.urls import include, path

from apps.common.views import healthz
from apps.tenants.api import TenantCreateView

urlpatterns = [
    path("healthz", healthz),
    path("admin/tenants", TenantCreateView.as_view(), name="tenants-admin-create"),
    path("admin/", include("apps.masterdata.urls")),
    path("django-admin/", admin.site.urls),
    path("auth/", include("dj_rest_auth.urls")),
    path("auth/registration/", include("dj_rest_auth.registration.urls")),
]
