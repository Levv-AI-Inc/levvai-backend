import json
import logging
import os
import time

from google.api_core.retry import Retry
from google.auth import default as google_auth_default
from google.auth.transport.requests import AuthorizedSession
from google.cloud import dns, tasks_v2

logger = logging.getLogger(__name__)


def _get_setting(name, default=None):
    return os.getenv(name, default)


def enqueue_domain_provision(domain, schema_name=None):
    if _get_setting("TENANT_DNS_MODE", "managed").lower() == "wildcard":
        logger.info("tenant DNS mode is wildcard; skipping domain provisioning")
        return

    if _get_setting("ENABLE_DOMAIN_PROVISIONING", "false").lower() != "true":
        logger.info("domain provisioning disabled")
        return

    project = _get_setting("GCP_PROJECT_ID")
    location = _get_setting("CLOUD_TASKS_LOCATION", "us-east1")
    queue = _get_setting("CLOUD_TASKS_QUEUE")
    service_url = _get_setting("CLOUD_RUN_URL")
    service_account_email = _get_setting("CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL")

    if not all([project, queue, service_url, service_account_email]):
        logger.warning("missing Cloud Tasks config; skipping domain provisioning task")
        return

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(project, location, queue)

    payload = {"domain": domain, "schema_name": schema_name}
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{service_url.rstrip('/')}/tasks/provision-domain",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(payload).encode(),
            "oidc_token": {"service_account_email": service_account_email},
        }
    }

    client.create_task(parent=parent, task=task)
    logger.info("enqueued domain provisioning task for %s", domain)


def provision_domain(domain):
    project = _get_setting("GCP_PROJECT_ID")
    region = _get_setting("CLOUD_RUN_REGION", "us-east1")
    service = _get_setting("CLOUD_RUN_SERVICE")
    dns_zone = _get_setting("CLOUD_DNS_ZONE")

    if not all([project, region, service, dns_zone]):
        raise RuntimeError("missing Cloud Run/DNS config for domain provisioning")

    credentials, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(credentials)

    parent = f"namespaces/{project}"
    base_url = f"https://{region}-run.googleapis.com"
    create_url = f"{base_url}/apis/domains.cloudrun.com/v1/{parent}/domainmappings"
    get_url = f"{base_url}/apis/domains.cloudrun.com/v1/{parent}/domainmappings/{domain}"
    body = {
        "apiVersion": "domains.cloudrun.com/v1",
        "kind": "DomainMapping",
        "metadata": {"name": domain, "namespace": project},
        "spec": {"routeName": service},
    }

    resp = session.post(create_url, json=body, timeout=30)
    if resp.status_code == 409:
        logger.info("domain mapping already exists for %s", domain)
    else:
        if resp.status_code >= 400:
            logger.error("failed to create domain mapping: %s", resp.text)
            resp.raise_for_status()
        logger.info("created domain mapping for %s", domain)

    records = _wait_for_domain_records(session, get_url)
    if records:
        _upsert_dns_records(dns_zone, records)
        logger.info("updated DNS records for %s", domain)
    else:
        logger.warning("no DNS records returned for %s", domain)


def _wait_for_domain_records(session, get_url):
    for _ in range(30):
        resp = session.get(get_url, timeout=30)
        if resp.status_code >= 400:
            logger.warning("failed to fetch domain mapping: %s", resp.text)
            time.sleep(5)
            continue
        mapping = resp.json()
        status = mapping.get("status", {})
        records = status.get("resourceRecords", [])
        if records:
            return records
        time.sleep(5)
    return []




def _normalize_record_name(name, zone_dns):
    name = (name or "").strip().lower()
    zone_dns = (zone_dns or "").strip().lower()

    if zone_dns and not zone_dns.endswith("."):
        zone_dns += "."

    if not name:
        return zone_dns or name

    if name == "@":
        return zone_dns or name

    if name.endswith("."):
        # If it's already a fully-qualified name within the zone, keep it.
        if zone_dns and name.endswith(zone_dns):
            return name
        # Otherwise treat it as a relative label.
        name = name[:-1]
        if not name:
            return zone_dns or name

    zone_no_dot = zone_dns[:-1] if zone_dns.endswith(".") else zone_dns
    if zone_no_dot and (name == zone_no_dot or name.endswith("." + zone_no_dot)):
        return f"{name}."

    return f"{name}.{zone_dns}" if zone_dns else f"{name}."


def _upsert_dns_records(zone_name, records):
    client = dns.Client()
    zone = client.zone(zone_name)
    try:
        zone.reload()
    except Exception:
        logger.warning("failed to load DNS zone metadata for %s", zone_name, exc_info=True)
    zone_dns = zone.dns_name or ""
    changes = zone.changes()

    # Build existing record index.
    existing = {}
    for record_set in zone.list_resource_record_sets():
        existing.setdefault((record_set.name, record_set.record_type), []).append(record_set)

    for record in records:
        name = _normalize_record_name(record["name"], zone_dns)
        rtype = record["type"]
        ttl = record.get("ttl", 300)
        rrdata = record.get("rrdata", [])
        if isinstance(rrdata, str):
            rrdata = [rrdata]

        for old in existing.get((name, rtype), []):
            changes.delete_record_set(old)

        new = zone.resource_record_set(name, rtype, ttl, rrdata)
        changes.add_record_set(new)

    changes.create()
    try:
        for _ in range(12):
            changes.reload()
            if getattr(changes, "status", "").lower() == "done":
                break
            time.sleep(5)
    except Exception:
        logger.warning("failed to wait for DNS change to complete", exc_info=True)
