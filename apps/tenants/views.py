import json
import logging
import os

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from apps.tenants.provisioning import provision_domain

logger = logging.getLogger(__name__)


def _verify_task_request(request):
    if os.getenv("CLOUD_TASKS_VERIFY_OIDC", "false").lower() != "true":
        return True

    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth.startswith("Bearer "):
        return False

    token = auth.split(" ", 1)[1]
    audience = os.getenv("CLOUD_TASKS_AUDIENCE")
    if not audience:
        return False

    id_token.verify_oauth2_token(token, google_requests.Request(), audience=audience)
    return True


@csrf_exempt
def provision_domain_task(request):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)

    try:
        if not _verify_task_request(request):
            return JsonResponse({"error": "unauthorized"}, status=401)
    except Exception:
        logger.exception("task token verification failed")
        return JsonResponse({"error": "unauthorized"}, status=401)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON"}, status=400)

    domain = (payload.get("domain") or "").strip().lower()
    if not domain:
        return JsonResponse({"error": "domain is required"}, status=400)

    provision_domain(domain)
    return JsonResponse({"status": "ok"})
