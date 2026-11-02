from django.db import models
from django.utils.dateparse import parse_datetime

from .fields import OrderedJSONField


class BadDocument(ValueError):
    pass


def dig(doc, path):
    if isinstance(path, str):
        return doc.get(path)
    value = doc
    for step in path:
        if isinstance(value, dict):
            value = value.get(step)
        elif isinstance(value, list) and isinstance(step, int) and step < len(value):
            value = value[step]
        else:
            return None
    return value


class StoreModel(models.Model):
    """
    One row per entity of 01 section 4.1, written by the Flow Studio Next app, which holds all the logic.

    `doc` holds the entity exactly as TypeScript wrote it, so reads return the same keys, the same key order, the same
    timestamp strings and the same null-versus-absent that were written. The other columns are copied out of `doc` on
    every write, for indexes and for reading the data in SQL. COLUMNS maps column to doc key.
    """

    id = models.CharField(primary_key=True, max_length=191)
    position = models.BigIntegerField(default=0)
    doc = OrderedJSONField()
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=255, null=True, blank=True)

    COLUMNS = {}
    FILTERS = {}
    REV_FIELD = None
    WRITE_OPS = frozenset({"insert", "upsert", "patch"})

    class Meta:
        abstract = True

    def apply_doc(self, doc):
        if not isinstance(doc, dict):
            raise BadDocument("doc must be an object")
        for column, path in self.COLUMNS.items():
            field = self._meta.get_field(column)
            value = dig(doc, path)
            if value is None and field.has_default():
                value = field.get_default()
            if value is None and not field.null:
                raise BadDocument(f"doc needs {path}")
            if value is not None and isinstance(field, models.DateTimeField):
                parsed = parse_datetime(value) if isinstance(value, str) else None
                if parsed is None:
                    raise BadDocument(f"{path} is not a date-time")
                value = parsed
            setattr(self, field.attname, value)
        self.doc = doc


class Workflow(StoreModel):
    """doc = the full Workflow object (02 section 4) without `versions`, which are rows of their own."""

    name = models.CharField(max_length=255)
    record_type = models.CharField(max_length=64)
    event = models.CharField(max_length=64)
    status = models.CharField(max_length=16)
    version = models.IntegerField(default=0)
    owner = models.CharField(max_length=255, null=True, blank=True)
    priority = models.IntegerField(null=True, blank=True)
    source = models.CharField(max_length=32, default="levv")
    effective = models.CharField(max_length=10, null=True, blank=True)
    draft_rev = models.IntegerField(default=1)

    COLUMNS = {
        "name": "name",
        "record_type": ("records", 0),
        "event": "event",
        "status": "status",
        "version": "version",
        "owner": "owner",
        "priority": "priority",
        "source": "source",
        "effective": "effective",
    }
    FILTERS = {"status": "status", "recordType": "record_type", "event": "event"}
    REV_FIELD = "draft_rev"

    class Meta:
        indexes = [models.Index(fields=["record_type", "event", "status"], name="fs_workflow_selection")]


class WorkflowVersion(StoreModel):
    """Published versions are never changed: a run pins one. Upsert exists for the seed, patch does not exist."""

    workflow = models.ForeignKey(Workflow, on_delete=models.PROTECT, related_name="versions")
    version = models.IntegerField()
    effective = models.CharField(max_length=10)
    published_by = models.CharField(max_length=255, null=True, blank=True)
    approved_by = models.CharField(max_length=255, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    COLUMNS = {
        "workflow_id": "workflowId",
        "version": "version",
        "effective": "effective",
        "published_by": "submittedBy",
        "approved_by": "approvedBy",
        "approved_at": "publishedAt",
    }
    FILTERS = {"workflowId": "workflow_id"}
    WRITE_OPS = frozenset({"insert", "upsert"})

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workflow", "version"], name="fs_version_once")]


class PublishRequest(StoreModel):
    workflow = models.ForeignKey(Workflow, on_delete=models.PROTECT, related_name="requests")
    go_live = models.CharField(max_length=10)
    requested_by = models.CharField(max_length=255)
    requested_at = models.DateTimeField()
    status = models.CharField(max_length=16)
    decided_by = models.CharField(max_length=255, null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    author_notified_at = models.DateTimeField(null=True, blank=True)

    COLUMNS = {
        "workflow_id": "workflowId",
        "go_live": "goLive",
        "requested_by": "requestedBy",
        "requested_at": "requestedAt",
        "status": "status",
        "decided_by": "decidedBy",
        "decided_at": "decidedAt",
        "author_notified_at": "authorNotifiedAt",
    }
    FILTERS = {"status": "status", "workflowId": "workflow_id", "requestedBy": "requested_by"}
    WRITE_OPS = frozenset({"insert", "patch"})


class ReferenceData(StoreModel):
    """id = the reference key. Stored as seeded or imported; normalised when read."""

    WRITE_OPS = frozenset({"upsert"})


class SampleRecord(StoreModel):
    WRITE_OPS = frozenset({"upsert"})


class HistoryRecord(StoreModel):
    WRITE_OPS = frozenset({"upsert"})


class BusinessRecord(StoreModel):
    type = models.CharField(max_length=64)
    source = models.CharField(max_length=32, default="levv")

    COLUMNS = {"type": "type", "source": "source"}
    FILTERS = {"type": "type"}

    class Meta:
        indexes = [models.Index(fields=["type"], name="fs_record_type")]


class WorkflowRun(StoreModel):
    """doc = the WorkflowRun (02 section 14) without `log`, which is RunEvent rows."""

    workflow = models.ForeignKey(Workflow, on_delete=models.PROTECT, related_name="runs")
    version = models.IntegerField()
    record_type = models.CharField(max_length=64)
    record_id = models.CharField(max_length=191)
    event = models.CharField(max_length=64)
    status = models.CharField(max_length=16)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    paused_by = models.CharField(max_length=255, null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    pause_reason = models.CharField(max_length=32, null=True, blank=True)
    stopped_by = models.CharField(max_length=255, null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    stop_reason = models.TextField(null=True, blank=True)
    rev = models.IntegerField(default=1)

    COLUMNS = {
        "workflow_id": "workflowId",
        "version": "version",
        "record_type": ("recordRef", "type"),
        "record_id": ("recordRef", "id"),
        "event": "event",
        "status": "status",
        "started_at": "startedAt",
        "ended_at": "endedAt",
        "paused_by": "pausedBy",
        "paused_at": "pausedAt",
        "pause_reason": "pauseReason",
        "stopped_by": "stoppedBy",
        "stopped_at": "stoppedAt",
        "stop_reason": "stopReason",
    }
    FILTERS = {"status": "status", "workflowId": "workflow_id", "recordId": "record_id", "recordType": "record_type"}
    REV_FIELD = "rev"
    WRITE_OPS = frozenset({"insert", "patch"})

    class Meta:
        indexes = [
            models.Index(fields=["status"], name="fs_run_status"),
            models.Index(fields=["record_type", "record_id"], name="fs_run_record"),
        ]


class RunEvent(StoreModel):
    """Append-only audit log."""

    run = models.ForeignKey(WorkflowRun, on_delete=models.PROTECT, related_name="events")
    at = models.DateTimeField()
    by = models.CharField(max_length=255)
    type = models.CharField(max_length=64)
    node_id = models.CharField(max_length=191, null=True, blank=True)

    COLUMNS = {"run_id": "runId", "at": "at", "by": "by", "type": "type", "node_id": "nodeId"}
    FILTERS = {"runId": "run_id"}
    WRITE_OPS = frozenset({"insert"})

    class Meta:
        indexes = [models.Index(fields=["run", "at"], name="fs_event_run_at")]


class WorkItem(StoreModel):
    run = models.ForeignKey(WorkflowRun, on_delete=models.PROTECT, related_name="work_items", null=True, blank=True)
    publish_request = models.ForeignKey(
        PublishRequest, on_delete=models.PROTECT, related_name="work_items", null=True, blank=True
    )
    node_id = models.CharField(max_length=191, null=True, blank=True)
    kind = models.CharField(max_length=32)
    assignee = models.CharField(max_length=255)
    on_behalf_of = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=16)
    remind_at = models.DateTimeField(null=True, blank=True)
    escalate_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()
    done_at = models.DateTimeField(null=True, blank=True)
    decision = models.CharField(max_length=32, null=True, blank=True)

    COLUMNS = {
        "run_id": "runId",
        "publish_request_id": "publishRequestId",
        "node_id": "nodeId",
        "kind": "kind",
        "assignee": "assignee",
        "on_behalf_of": "onBehalfOf",
        "status": "status",
        "remind_at": "remindAt",
        "escalate_at": "escalateAt",
        "due_at": "dueAt",
        "created_at": "createdAt",
        "done_at": "doneAt",
        "decision": "decision",
    }
    FILTERS = {
        "assignee": "assignee",
        "status": "status",
        "runId": "run_id",
        "publishRequestId": "publish_request_id",
        "kind": "kind",
    }
    WRITE_OPS = frozenset({"insert", "patch", "updateWhere"})

    class Meta:
        indexes = [
            models.Index(fields=["assignee", "status"], name="fs_item_inbox"),
            models.Index(fields=["run"], name="fs_item_run"),
        ]


class LedgerLine(StoreModel):
    worker_key = models.CharField(max_length=255)
    run = models.ForeignKey(WorkflowRun, on_delete=models.PROTECT, related_name="ledger_lines")
    node_id = models.CharField(max_length=191)
    gives = models.CharField(max_length=255)
    disp = models.CharField(max_length=16)
    reverse = models.CharField(max_length=255, null=True, blank=True)
    reverse_when = models.CharField(max_length=16, null=True, blank=True)
    reverse_by = models.CharField(max_length=255, null=True, blank=True)
    system_ref = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=16, default="open")
    created_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)

    COLUMNS = {
        "worker_key": "workerKey",
        "run_id": "runId",
        "node_id": "nodeId",
        "gives": "gives",
        "disp": "disp",
        "reverse": "reverse",
        "reverse_when": "reverseWhen",
        "reverse_by": "reverseBy",
        "system_ref": "systemRef",
        "status": "status",
        "created_at": "createdAt",
        "closed_at": "closedAt",
    }
    FILTERS = {"workerKey": "worker_key", "status": "status", "runId": "run_id"}
    WRITE_OPS = frozenset({"insert", "patch"})

    class Meta:
        indexes = [models.Index(fields=["worker_key", "status"], name="fs_ledger_worker")]


class OutboxMessage(StoreModel):
    run = models.ForeignKey(WorkflowRun, on_delete=models.PROTECT, related_name="outbox", null=True, blank=True)
    to = models.CharField(max_length=255)
    channel = models.CharField(max_length=16)
    subject = models.TextField()
    created_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)

    COLUMNS = {
        "run_id": "runId",
        "to": "to",
        "channel": "channel",
        "subject": "subject",
        "created_at": "createdAt",
        "sent_at": "sentAt",
    }
    FILTERS = {"runId": "run_id", "to": "to"}
    WRITE_OPS = frozenset({"insert", "patch"})


class InboundEvent(StoreModel):
    source = models.CharField(max_length=32)
    record_type = models.CharField(max_length=64)
    record_id = models.CharField(max_length=191)
    event = models.CharField(max_length=64)
    received_at = models.DateTimeField()
    processed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=32, null=True, blank=True)

    COLUMNS = {
        "source": "source",
        "record_type": "recordType",
        "record_id": "recordId",
        "event": "event",
        "received_at": "receivedAt",
        "processed_at": "processedAt",
        "outcome": "outcome",
    }
    FILTERS = {"recordId": "record_id", "source": "source", "outcome": "outcome"}
    WRITE_OPS = frozenset({"insert", "patch"})


class RunClaim(StoreModel):
    """
    One row per workflow, version, record and event: which run holds it. Two copies of an event arrive at once, both find
    no running run, and only one insert of this row can succeed, so only one run starts (05 section 3.1 step 4).
    """

    run_id = models.CharField(max_length=191)

    COLUMNS = {"run_id": "runId"}
    WRITE_OPS = frozenset({"insert", "patch"})


COLLECTIONS = {
    "workflows": Workflow,
    "versions": WorkflowVersion,
    "requests": PublishRequest,
    "reference": ReferenceData,
    "samples": SampleRecord,
    "history": HistoryRecord,
    "records": BusinessRecord,
    "runs": WorkflowRun,
    "events": RunEvent,
    "items": WorkItem,
    "ledger": LedgerLine,
    "outbox": OutboxMessage,
    "inbound": InboundEvent,
    "claims": RunClaim,
}
