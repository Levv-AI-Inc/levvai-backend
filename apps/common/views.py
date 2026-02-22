import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


def healthz(request):
    logger.info("healthz ok")
    return JsonResponse({"status": "ok"})
