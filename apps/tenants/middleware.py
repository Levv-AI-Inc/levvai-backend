from django.http import JsonResponse
from django_tenants.utils import schema_context

from apps.tenants.models import Domain


class TenantExistenceMiddleware:
    """
    Lightweight host validation endpoint used by the frontend middleware.
    It runs before TenantMainMiddleware so invalid hosts can still be checked.
    """

    CHECK_PATH = "/auth/password/tenant-exists"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == self.CHECK_PATH:
            host = self._normalized_host(request)
            exists = False

            if host:
                with schema_context("public"):
                    domain = Domain.objects.filter(domain=host).first()
                if domain is not None:
                    exists = True

            return JsonResponse(
                {
                    "exists": exists,
                    "host": host,
                }
            )

        return self.get_response(request)

    @staticmethod
    def _normalized_host(request):
        host = (request.GET.get("host") or "").strip().lower()
        if not host:
            forwarded_host = (request.META.get("HTTP_X_FORWARDED_HOST") or "").split(",")[0].strip()
            host = (forwarded_host or request.get_host()).split(":")[0].strip().lower()

        return host.rstrip(".")
