import json

from django.conf import settings
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Max
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership

from .auth import StoreAccess, check_store_token
from .models import COLLECTIONS, BadDocument, Workflow, WorkflowRun

LOCAL_DATABASE_HOSTS = {"localhost", "127.0.0.1", "::1"}


class WriteConflict(Exception):
    """Ends the transaction: nothing in the request is applied."""

    def __init__(self, reason, current=None):
        super().__init__(reason)
        self.reason = reason
        self.current = current


class StoreView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [StoreAccess]

    def initial(self, request, *args, **kwargs):
        check_store_token(request)
        super().initial(request, *args, **kwargs)


def model_for(collection):
    model = COLLECTIONS.get(collection)
    if model is None:
        raise NotFound(f"Unknown collection {collection}.")
    return model


def row_json(row):
    data = {"id": row.pk, "doc": row.doc, "updatedAt": row.updated_at.isoformat(), "updatedBy": row.updated_by}
    if row.REV_FIELD:
        data["rev"] = getattr(row, row.REV_FIELD)
    if isinstance(row, Workflow):
        data["versions"] = [version.doc for version in sorted(row.versions.all(), key=lambda v: v.version)]
    if isinstance(row, WorkflowRun):
        data["events"] = [event.doc for event in sorted(row.events.all(), key=lambda e: (e.position, e.pk))]
    return data


def with_related(queryset):
    if queryset.model is Workflow:
        return queryset.prefetch_related("versions")
    if queryset.model is WorkflowRun:
        return queryset.prefetch_related("events")
    return queryset


class CollectionView(StoreView):
    def get(self, request, collection):
        model = model_for(collection)
        filters = {}
        for key in request.query_params:
            column = model.FILTERS.get(key)
            if column is None:
                raise ValidationError({key: "Not a filter of this collection."})
            filters[f"{column}__in"] = request.query_params.getlist(key)
        rows = with_related(model.objects.filter(**filters).order_by("position", "pk"))
        return Response({"rows": [row_json(row) for row in rows]})


class RowView(StoreView):
    def get(self, request, collection, row_id):
        model = model_for(collection)
        row = with_related(model.objects.filter(pk=row_id)).first()
        if row is None:
            raise NotFound()
        return Response(row_json(row))


def next_position(model):
    return (model.objects.aggregate(top=Max("position"))["top"] or 0) + 1


def locked(model, row_id):
    return model.objects.select_for_update().filter(pk=row_id).first()


def write_id(write):
    row_id = write.get("id")
    if not isinstance(row_id, str) or not row_id:
        raise ValidationError({"id": "A write needs an id."})
    return row_id


def insert(model, write):
    row_id = write_id(write)
    if model.objects.filter(pk=row_id).exists():
        raise WriteConflict("exists")
    row = model(pk=row_id, position=write.get("position") or next_position(model), updated_by=write.get("by"))
    row.apply_doc(write.get("doc"))
    row.save(force_insert=True)
    return result_of(row)


def upsert(model, write):
    row_id = write_id(write)
    row = locked(model, row_id)
    if row is None:
        return insert(model, write)
    # Key order counts as a difference, so this compares the text rather than the dicts.
    if json.dumps(row.doc) == json.dumps(write.get("doc")):
        return result_of(row)
    if "position" in write:
        row.position = write["position"]
    row.updated_by = write.get("by")
    row.apply_doc(write.get("doc"))
    if row.REV_FIELD:
        setattr(row, row.REV_FIELD, getattr(row, row.REV_FIELD) + 1)
    row.save()
    return result_of(row)


def merged(row, write):
    doc = dict(row.doc)
    changes = write.get("set") or {}
    if not isinstance(changes, dict):
        raise ValidationError({"set": "Must be an object."})
    doc.update(changes)
    for key in write.get("unset") or []:
        doc.pop(key, None)
    row.updated_by = write.get("by", row.updated_by)
    row.apply_doc(doc)


def patch(model, write):
    row = locked(model, write_id(write))
    if row is None:
        raise WriteConflict("missing")
    if "expectRev" in write:
        if not row.REV_FIELD:
            raise ValidationError({"expectRev": "This collection has no rev."})
        current = getattr(row, row.REV_FIELD)
        if current != write["expectRev"]:
            raise WriteConflict("rev", current)
    for key, value in (write.get("expect") or {}).items():
        if row.doc.get(key) != value:
            raise WriteConflict("expect", row.doc.get(key))
    merged(row, write)
    if write.get("bumpRev"):
        if not row.REV_FIELD:
            raise ValidationError({"bumpRev": "This collection has no rev."})
        setattr(row, row.REV_FIELD, getattr(row, row.REV_FIELD) + 1)
    row.save()
    return result_of(row)


def update_where(model, write):
    filters = {}
    for key, value in (write.get("where") or {}).items():
        column = model.FILTERS.get(key)
        if column is None:
            raise ValidationError({key: "Not a filter of this collection."})
        filters[f"{column}__in"] = value if isinstance(value, list) else [value]
    if not filters:
        raise ValidationError({"where": "updateWhere needs a filter."})
    rows = list(model.objects.select_for_update().filter(**filters).order_by("position", "pk"))
    for row in rows:
        merged(row, write)
        row.save()
    return {"count": len(rows)}


def result_of(row):
    return {"id": row.pk, "rev": getattr(row, row.REV_FIELD)} if row.REV_FIELD else {"id": row.pk}


OPERATIONS = {"insert": insert, "upsert": upsert, "patch": patch, "updateWhere": update_where}


def apply_write(write):
    if not isinstance(write, dict):
        raise ValidationError({"writes": "Each write must be an object."})
    model = COLLECTIONS.get(write.get("collection"))
    if model is None:
        raise ValidationError({"collection": f"Unknown collection {write.get('collection')}."})
    op = write.get("op")
    if op not in OPERATIONS:
        raise ValidationError({"op": f"Unknown operation {op}."})
    if op not in model.WRITE_OPS:
        raise ValidationError({"op": f"{write['collection']} does not allow {op}."})
    return OPERATIONS[op](model, write)


class TxView(StoreView):
    """Applies a list of writes in one transaction. Any conflict means none of them happened."""

    def post(self, request):
        writes = request.data.get("writes") if isinstance(request.data, dict) else None
        if not isinstance(writes, list) or not writes:
            raise ValidationError({"writes": "Must be a non-empty list."})
        index = 0
        results = []
        try:
            with transaction.atomic():
                for index, write in enumerate(writes):
                    results.append(apply_write(write))
        except BadDocument as error:
            raise ValidationError({"doc": f"Write {index}: {error}"})
        except WriteConflict as conflict:
            return Response({"conflict": True, "index": index, "reason": conflict.reason, "current": conflict.current}, status=409)
        except IntegrityError:
            return Response({"conflict": True, "index": index, "reason": "integrity", "current": None}, status=409)
        except OperationalError as error:
            # Postgres ended this transaction to break a deadlock (40P01). Nothing was applied, so the caller retries.
            if getattr(getattr(error, "__cause__", None), "pgcode", None) != "40P01":
                raise
            return Response({"conflict": True, "index": index, "reason": "integrity", "current": None}, status=409)
        return Response({"results": results})


class ActorsView(StoreView):
    """Active admins of this tenant: Next needs them for "is there anyone else who can approve" (01 section 6.1)."""

    def get(self, request):
        memberships = Membership.objects.filter(
            tenant_id=request.tenant.id,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
            role=Membership.ROLE_ADMIN,
        ).select_related("user")
        return Response(
            {
                "actors": [
                    {
                        "id": str(membership.user_id),
                        "email": membership.user.email,
                        "role": membership.role,
                    }
                    for membership in memberships
                ]
            }
        )


class MembersView(StoreView):
    """Active members of this tenant, by the id a link keys on: who someone with reference.import can link to a person."""

    def get(self, request):
        memberships = Membership.objects.filter(
            tenant_id=request.tenant.id,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        ).select_related("user")
        return Response({"members": [{"id": str(membership.user_id), "email": membership.user.email, "role": membership.role} for membership in memberships]})


class SeedTargetView(StoreView):
    """Lets `npm run seed:flow-studio` refuse a database that is not on this machine, without exposing where it is."""

    def get(self, request):
        host = settings.DATABASES["default"].get("HOST") or "localhost"
        return Response({"schema": request.tenant.schema_name, "databaseIsLocal": host in LOCAL_DATABASE_HOSTS})
