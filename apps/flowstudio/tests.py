import json

from django.db import connection
from django.test import Client, TestCase, override_settings

from apps.accounts.models import Membership, User, WorkerEngagement, WorkerProfile
from apps.accounts.session_scope import SESSION_TENANT_ID_KEY
from apps.tenants.models import Domain, Tenant

from .fields import OrderedJSONField
from .models import COLLECTIONS, Workflow

TOKEN = "test-store-token"
HOST_A = "flow-a.levvai.test"
HOST_B = "flow-b.levvai.test"
PUBLIC_HOST = "flow-public.levvai.test"
BASE = "/flowstudio-store"


def workflow_doc(workflow_id="wf-1", **overrides):
    doc = {
        "id": workflow_id,
        "name": "A workflow",
        "records": ["work_order"],
        "event": "submitted",
        "scope": {},
        "status": "draft",
        "version": 0,
        "effective": "2026-12-01",
        "owner": "Procurement operations",
        "priority": None,
        "draft": {"nodes": {"T": {"id": "T"}, "region": {"id": "region"}, "bu": {"id": "bu"}, "n10": {"id": "n10"}, "n2": {"id": "n2"}, "END": {"id": "END"}}},
        "published": None,
    }
    doc.update(overrides)
    return doc


def insert_workflow(workflow_id="wf-1", **overrides):
    return {"op": "insert", "collection": "workflows", "id": workflow_id, "doc": workflow_doc(workflow_id, **overrides), "by": "Flow Admin"}


@override_settings(FLOW_STUDIO_STORE_TOKEN=TOKEN, ALLOWED_HOSTS=["*"])
class StoreTestCase(TestCase):
    """Two real tenant schemas, reached through the real middleware by Host header, as Next reaches them."""

    @classmethod
    def setUpTestData(cls):
        connection.set_schema_to_public()
        Domain.objects.get_or_create(
            domain=PUBLIC_HOST,
            defaults={"tenant": Tenant.objects.get_or_create(schema_name="public", defaults={"name": "Public"})[0], "is_primary": False},
        )
        cls.tenant_a = cls._tenant("flow_a", HOST_A)
        cls.tenant_b = cls._tenant("flow_b", HOST_B)
        cls.member = cls._user("member@example.com")
        Membership.objects.create(user=cls.member, tenant=cls.tenant_a, role=Membership.ROLE_ADMIN, status=Membership.STATUS_ACTIVE, is_active=True)

    @classmethod
    def _tenant(cls, schema, host):
        tenant = Tenant(schema_name=schema, name=schema)
        tenant.save(verbosity=0)
        Domain.objects.create(domain=host, tenant=tenant, is_primary=True)
        return tenant

    @classmethod
    def _user(cls, email, first_name="Member", last_name="One"):
        return User.objects.create_user(username=email, email=email, password="Password123!", first_name=first_name, last_name=last_name)

    def tearDown(self):
        connection.set_schema_to_public()

    def get(self, path, host=HOST_A, token=TOKEN, client=None):
        headers = {"HTTP_HOST": host}
        if token is not None:
            headers["HTTP_X_LEVV_STORE_TOKEN"] = token
        return (client or Client()).get(f"{BASE}{path}", **headers)

    def post(self, path, body, host=HOST_A, token=TOKEN, client=None, **extra):
        headers = {"HTTP_HOST": host, **extra}
        if token is not None:
            headers["HTTP_X_LEVV_STORE_TOKEN"] = token
        return (client or Client()).post(f"{BASE}{path}", data=json.dumps(body), content_type="application/json", **headers)

    def tx(self, *writes, **kwargs):
        return self.post("/tx/", {"writes": list(writes)}, **kwargs)

    def session_client(self, user, tenant, csrf=False):
        client = Client(enforce_csrf_checks=csrf)
        client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        session = client.session
        session[SESSION_TENANT_ID_KEY] = tenant.id
        session.save()
        return client


class StoreTokenTests(StoreTestCase):
    def test_every_route_refuses_a_missing_or_wrong_token(self):
        for path in ("/c/workflows/", "/c/workflows/wf-1/", "/actors/", "/seed-target/"):
            self.assertEqual(self.get(path, token=None).status_code, 403, path)
            self.assertEqual(self.get(path, token="wrong").status_code, 403, path)
        self.assertEqual(self.tx(insert_workflow(), token=None).status_code, 403)
        self.assertEqual(self.tx(insert_workflow(), token="wrong").status_code, 403)
        self.assertEqual(self.get("/c/workflows/").json()["rows"], [])

    def test_a_right_token_is_accepted(self):
        self.assertEqual(self.get("/c/workflows/").status_code, 200)

    @override_settings(FLOW_STUDIO_STORE_TOKEN="")
    def test_the_store_fails_closed_when_the_token_is_not_configured(self):
        self.assertEqual(self.get("/c/workflows/").status_code, 503)
        self.assertEqual(self.get("/c/workflows/", token="").status_code, 503)
        self.assertEqual(self.tx(insert_workflow()).status_code, 503)

    def test_a_valid_member_session_without_the_token_is_refused(self):
        client = self.session_client(self.member, self.tenant_a)
        self.assertEqual(self.get("/c/workflows/", token=None, client=client).status_code, 403)
        self.assertEqual(self.tx(insert_workflow(), token=None, client=client).status_code, 403)


class SessionTests(StoreTestCase):
    def test_a_member_session_can_read_without_csrf(self):
        client = self.session_client(self.member, self.tenant_a, csrf=True)
        self.assertEqual(self.get("/c/workflows/", client=client).status_code, 200)

    def test_a_member_session_needs_csrf_to_write(self):
        client = self.session_client(self.member, self.tenant_a, csrf=True)
        refused = self.tx(insert_workflow(), client=client)
        self.assertEqual(refused.status_code, 403)
        self.assertIn("CSRF", refused.json()["detail"])
        self.assertEqual(self.get("/c/workflows/").json()["rows"], [])

        client.cookies["csrftoken"] = "a" * 32
        allowed = self.tx(insert_workflow(), client=client, HTTP_X_CSRFTOKEN="a" * 32)
        self.assertEqual(allowed.status_code, 200)

    def test_a_wrong_origin_is_refused(self):
        client = self.session_client(self.member, self.tenant_a, csrf=True)
        client.cookies["csrftoken"] = "a" * 32
        refused = self.tx(insert_workflow(), client=client, HTTP_X_CSRFTOKEN="a" * 32, HTTP_ORIGIN="https://evil.example")
        self.assertEqual(refused.status_code, 403)
        allowed = self.tx(insert_workflow(), client=client, HTTP_X_CSRFTOKEN="a" * 32, HTTP_ORIGIN=f"http://{HOST_A}")
        self.assertEqual(allowed.status_code, 200)

    def test_a_cookie_that_is_not_a_session_is_refused_not_treated_as_a_service_call(self):
        client = Client()
        client.cookies["sessionid"] = "not-a-real-session"
        self.assertEqual(self.get("/c/workflows/", client=client).status_code, 403)
        self.assertEqual(self.tx(insert_workflow(), client=client).status_code, 403)

    def test_a_user_with_no_membership_is_refused(self):
        outsider = self._user("outsider@example.com")
        client = self.session_client(outsider, self.tenant_a)
        self.assertEqual(self.get("/c/workflows/", client=client).status_code, 403)

    def test_an_active_worker_without_a_membership_is_refused(self):
        worker = self._user("worker@example.com")
        profile = WorkerProfile.objects.create(user=worker, preferred_name="A Worker")
        WorkerEngagement.objects.create(
            worker_profile=profile,
            tenant=self.tenant_a,
            engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
            work_order_number="WO-1",
            supplier_id=1,
            supplier_name="Supplier",
            client_name="Client",
            role_name="Role",
            status=WorkerEngagement.STATUS_ACTIVE,
        )
        client = self.session_client(worker, self.tenant_a)
        self.assertEqual(self.get("/c/workflows/", client=client).status_code, 403)

    def test_a_session_bound_to_one_tenant_is_refused_on_another(self):
        Membership.objects.create(user=self.member, tenant=self.tenant_b, role=Membership.ROLE_ADMIN, status=Membership.STATUS_ACTIVE, is_active=True)
        client = self.session_client(self.member, self.tenant_a)
        self.assertEqual(self.get("/c/workflows/", client=client, host=HOST_A).status_code, 200)
        self.assertEqual(self.get("/c/workflows/", client=client, host=HOST_B).status_code, 403)


class TenantIsolationTests(StoreTestCase):
    def test_rows_written_on_one_tenant_are_invisible_on_another(self):
        self.assertEqual(self.tx(insert_workflow("wf-a"), host=HOST_A).status_code, 200)
        self.assertEqual(self.tx(insert_workflow("wf-b", name="B"), host=HOST_B).status_code, 200)
        self.assertEqual([r["id"] for r in self.get("/c/workflows/", host=HOST_A).json()["rows"]], ["wf-a"])
        self.assertEqual([r["id"] for r in self.get("/c/workflows/", host=HOST_B).json()["rows"]], ["wf-b"])
        self.assertEqual(self.get("/c/workflows/wf-a/", host=HOST_B).status_code, 404)

    def test_the_same_id_is_independent_per_tenant(self):
        self.tx(insert_workflow("wf-1", name="On A"), host=HOST_A)
        self.tx(insert_workflow("wf-1", name="On B"), host=HOST_B)
        self.tx({"op": "patch", "collection": "workflows", "id": "wf-1", "set": {"name": "Changed on A"}, "expectRev": 1, "bumpRev": True}, host=HOST_A)
        on_a = self.get("/c/workflows/wf-1/", host=HOST_A).json()
        on_b = self.get("/c/workflows/wf-1/", host=HOST_B).json()
        self.assertEqual((on_a["doc"]["name"], on_a["rev"]), ("Changed on A", 2))
        self.assertEqual((on_b["doc"]["name"], on_b["rev"]), ("On B", 1))

    def test_a_write_cannot_reach_another_tenants_row(self):
        self.tx(insert_workflow("wf-a"), host=HOST_A)
        refused = self.tx({"op": "patch", "collection": "workflows", "id": "wf-a", "set": {"name": "x"}}, host=HOST_B)
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["reason"], "missing")
        self.assertEqual(self.get("/c/workflows/wf-a/", host=HOST_A).json()["doc"]["name"], "A workflow")

    def test_actors_are_this_tenants_active_admins_only(self):
        other_admin = self._user("b-admin@example.com", "Bea", "Other")
        Membership.objects.create(user=other_admin, tenant=self.tenant_b, role=Membership.ROLE_ADMIN, status=Membership.STATUS_ACTIVE, is_active=True)
        viewer = self._user("viewer@example.com")
        Membership.objects.create(user=viewer, tenant=self.tenant_a, role=Membership.ROLE_READ_ONLY, status=Membership.STATUS_ACTIVE, is_active=True)
        invited = self._user("invited@example.com")
        Membership.objects.create(user=invited, tenant=self.tenant_a, role=Membership.ROLE_ADMIN, status=Membership.STATUS_INVITED, is_active=True)
        self.assertEqual([a["email"] for a in self.get("/actors/", host=HOST_A).json()["actors"]], ["member@example.com"])
        self.assertEqual([a["email"] for a in self.get("/actors/", host=HOST_B).json()["actors"]], ["b-admin@example.com"])

    def test_an_unknown_host_gets_404(self):
        self.assertEqual(self.get("/c/workflows/", host="nobody.levvai.test").status_code, 404)

    def test_the_public_schema_has_no_store(self):
        self.assertEqual(self.get("/c/workflows/", host=PUBLIC_HOST).status_code, 404)
        self.assertEqual(self.tx(insert_workflow(), host=PUBLIC_HOST).status_code, 404)

    def test_the_tables_exist_only_in_tenant_schemas(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT table_schema FROM information_schema.tables WHERE table_name = 'flowstudio_workflow'")
            schemas = {row[0] for row in cursor.fetchall()}
        self.assertIn("flow_a", schemas)
        self.assertIn("flow_b", schemas)
        self.assertNotIn("public", schemas)


class KeyOrderTests(StoreTestCase):
    def test_every_doc_column_is_postgres_json_not_jsonb(self):
        for model in COLLECTIONS.values():
            self.assertIsInstance(model._meta.get_field("doc"), OrderedJSONField)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name, data_type FROM information_schema.columns WHERE table_schema = 'flow_a' AND column_name = 'doc'"
            )
            types = dict(cursor.fetchall())
        self.assertEqual(len(types), len(COLLECTIONS))
        self.assertEqual(set(types.values()), {"json"})

    def test_key_order_survives_the_round_trip(self):
        self.assertEqual(self.tx(insert_workflow()).status_code, 200)
        doc = self.get("/c/workflows/wf-1/").json()["doc"]
        self.assertEqual(list(doc["draft"]["nodes"]), ["T", "region", "bu", "n10", "n2", "END"])
        self.assertEqual(list(doc), list(workflow_doc()))

    def test_key_order_survives_a_patch(self):
        self.tx(insert_workflow())
        nodes = {"T": {"id": "T"}, "z": {"id": "z"}, "a": {"id": "a"}, "n10": {"id": "n10"}, "n2": {"id": "n2"}, "END": {"id": "END"}}
        self.tx({"op": "patch", "collection": "workflows", "id": "wf-1", "set": {"draft": {"nodes": nodes}}, "expectRev": 1, "bumpRev": True})
        doc = self.get("/c/workflows/wf-1/").json()["doc"]
        self.assertEqual(list(doc["draft"]["nodes"]), list(nodes))
        self.assertEqual(list(doc), list(workflow_doc()))

    def test_null_absent_floats_and_text_survive(self):
        self.tx(insert_workflow(extra={"nothing": None, "ratio": 0.1, "whole": 3, "quote": "“it’s”"}))
        extra = self.get("/c/workflows/wf-1/").json()["doc"]["extra"]
        self.assertEqual(extra, {"nothing": None, "ratio": 0.1, "whole": 3, "quote": "“it’s”"})
        self.assertNotIn("missing", extra)


class ConcurrencyTests(StoreTestCase):
    def setUp(self):
        self.tx(insert_workflow())

    def save(self, rev, name="Saved"):
        return self.tx({"op": "patch", "collection": "workflows", "id": "wf-1", "set": {"name": name}, "expectRev": rev, "bumpRev": True, "by": "Flow Admin"})

    def test_a_current_rev_saves_and_moves_on(self):
        response = self.save(1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [{"id": "wf-1", "rev": 2}])
        row = self.get("/c/workflows/wf-1/").json()
        self.assertEqual((row["doc"]["name"], row["rev"], row["updatedBy"]), ("Saved", 2, "Flow Admin"))

    def test_a_stale_rev_conflicts_and_changes_nothing(self):
        self.save(1, "First")
        response = self.save(1, "Second")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"conflict": True, "index": 0, "reason": "rev", "current": 2})
        self.assertEqual(self.get("/c/workflows/wf-1/").json()["doc"]["name"], "First")

    def test_a_patch_without_bump_leaves_the_rev(self):
        self.tx({"op": "patch", "collection": "workflows", "id": "wf-1", "set": {"status": "inactive"}})
        row = self.get("/c/workflows/wf-1/").json()
        self.assertEqual((row["doc"]["status"], row["rev"]), ("inactive", 1))
        self.assertEqual(self.get("/c/workflows/?status=inactive").json()["rows"][0]["id"], "wf-1")

    def test_expect_guards_a_doc_value(self):
        guarded = {"op": "patch", "collection": "workflows", "id": "wf-1", "set": {"pending": {"submittedBy": "A"}}, "expect": {"pending": None}}
        self.assertEqual(self.tx(guarded).status_code, 200)
        response = self.tx(guarded)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["reason"], "expect")

    def test_a_conflict_rolls_back_every_write_in_the_transaction(self):
        response = self.tx(
            insert_workflow("wf-2"),
            {"op": "patch", "collection": "workflows", "id": "wf-1", "set": {"name": "x"}},
            {"op": "patch", "collection": "workflows", "id": "wf-1", "set": {"name": "y"}, "expectRev": 99},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["index"], 2)
        rows = self.get("/c/workflows/").json()["rows"]
        self.assertEqual([r["id"] for r in rows], ["wf-1"])
        self.assertEqual(rows[0]["doc"]["name"], "A workflow")

    def test_a_duplicate_insert_conflicts(self):
        response = self.tx(insert_workflow())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["reason"], "exists")

    def test_a_patch_on_a_missing_row_conflicts(self):
        response = self.tx({"op": "patch", "collection": "workflows", "id": "nope", "set": {"name": "x"}})
        self.assertEqual(response.json()["reason"], "missing")


class WriteRulesTests(StoreTestCase):
    def setUp(self):
        self.tx(insert_workflow())

    def version_doc(self, version=1):
        return {"workflowId": "wf-1", "version": version, "nodes": {}, "scope": {}, "name": "A", "effective": "2026-10-20", "publishedAt": "2026-10-20T09:00:00Z", "submittedBy": "A", "approvedBy": "B"}

    def run_doc(self):
        return {"id": "run-1", "workflowId": "wf-1", "version": 1, "recordRef": {"type": "work_order", "id": "WO-1"}, "event": "submitted", "status": "running", "startedAt": "2026-10-20T09:00:00Z", "recordSnapshot": {}, "plan": {}, "steps": {}}

    def event_doc(self, event_id):
        return {"id": event_id, "runId": "run-1", "at": "2026-10-20T09:00:00Z", "by": "Levv", "type": "started", "m": "Started"}

    def test_a_published_version_cannot_be_changed_twice(self):
        insert = {"op": "insert", "collection": "versions", "id": "wf-1:v1", "doc": self.version_doc()}
        self.assertEqual(self.tx(insert).status_code, 200)
        self.assertEqual(self.tx(insert).status_code, 409)
        self.assertEqual(self.tx({"op": "patch", "collection": "versions", "id": "wf-1:v1", "set": {"name": "x"}}).status_code, 400)
        duplicate = {"op": "insert", "collection": "versions", "id": "other", "doc": self.version_doc()}
        self.assertEqual(self.tx(duplicate).json()["reason"], "integrity")
        self.assertEqual([v["version"] for v in self.get("/c/workflows/wf-1/").json()["versions"]], [1])

    def test_versions_arrive_in_order_with_their_workflow(self):
        self.tx(
            {"op": "insert", "collection": "versions", "id": "wf-1:v2", "doc": self.version_doc(2)},
            {"op": "insert", "collection": "versions", "id": "wf-1:v1", "doc": self.version_doc(1)},
        )
        self.assertEqual([v["version"] for v in self.get("/c/workflows/").json()["rows"][0]["versions"]], [1, 2])

    def test_run_events_are_append_only_and_come_with_the_run(self):
        self.tx({"op": "insert", "collection": "runs", "id": "run-1", "doc": self.run_doc()})
        self.tx(
            {"op": "insert", "collection": "events", "id": "e-2", "doc": self.event_doc("e-2")},
            {"op": "insert", "collection": "events", "id": "e-1", "doc": self.event_doc("e-1")},
        )
        self.assertEqual(self.tx({"op": "patch", "collection": "events", "id": "e-1", "set": {"m": "x"}}).status_code, 400)
        self.assertEqual(self.tx({"op": "upsert", "collection": "events", "id": "e-1", "doc": {}}).status_code, 400)
        run = self.get("/c/runs/run-1/").json()
        self.assertEqual([e["id"] for e in run["events"]], ["e-2", "e-1"])

    def test_run_rev_guards_updates(self):
        self.tx({"op": "insert", "collection": "runs", "id": "run-1", "doc": self.run_doc()})
        ok = self.tx({"op": "patch", "collection": "runs", "id": "run-1", "set": {"status": "paused", "pausedAt": "2026-10-20T10:00:00Z"}, "expectRev": 1, "bumpRev": True})
        self.assertEqual(ok.json()["results"], [{"id": "run-1", "rev": 2}])
        self.assertEqual(self.tx({"op": "patch", "collection": "runs", "id": "run-1", "set": {"status": "running"}, "expectRev": 1, "bumpRev": True}).status_code, 409)
        self.assertEqual(self.get("/c/runs/?status=paused").json()["rows"][0]["rev"], 2)

    def test_update_where_changes_only_the_matching_items(self):
        self.tx({"op": "insert", "collection": "runs", "id": "run-1", "doc": self.run_doc()})
        def item(item_id, status):
            return {"op": "insert", "collection": "items", "id": item_id, "doc": {"id": item_id, "kind": "task", "runId": "run-1", "nodeId": "n", "publishRequestId": None, "assignee": "Raj Patel", "status": status, "createdAt": "2026-10-20T09:00:00Z"}}
        self.tx(item("i-1", "open"), item("i-2", "open"), item("i-3", "done"))
        response = self.tx({"op": "updateWhere", "collection": "items", "where": {"runId": "run-1", "status": ["open"]}, "set": {"status": "held"}})
        self.assertEqual(response.json()["results"], [{"count": 2}])
        self.assertEqual({r["id"] for r in self.get("/c/items/?status=held&assignee=Raj%20Patel").json()["rows"]}, {"i-1", "i-2"})
        self.assertEqual(self.tx({"op": "updateWhere", "collection": "items", "where": {}, "set": {"status": "held"}}).status_code, 400)

    def test_upsert_is_idempotent_and_only_moves_the_rev_on_a_real_change(self):
        doc = workflow_doc("wf-seed")
        write = {"op": "upsert", "collection": "workflows", "id": "wf-seed", "doc": doc, "position": 5}
        self.tx(write)
        self.tx(write)
        self.assertEqual(self.get("/c/workflows/wf-seed/").json()["rev"], 1)
        self.tx({**write, "doc": workflow_doc("wf-seed", name="Changed")})
        self.assertEqual(self.get("/c/workflows/wf-seed/").json()["rev"], 2)

    def test_rows_list_in_position_order(self):
        for row_id, position in (("s-b", 2), ("s-c", 3), ("s-a", 1)):
            self.tx({"op": "upsert", "collection": "samples", "id": row_id, "doc": {"id": row_id}, "position": position})
        self.assertEqual([r["id"] for r in self.get("/c/samples/").json()["rows"]], ["s-a", "s-b", "s-c"])

    def test_a_new_row_goes_after_the_seeded_ones(self):
        self.tx({"op": "upsert", "collection": "workflows", "id": "wf-seed", "doc": workflow_doc("wf-seed"), "position": 50})
        self.tx(insert_workflow("nf101"))
        self.assertEqual([r["id"] for r in self.get("/c/workflows/").json()["rows"]], ["wf-1", "wf-seed", "nf101"])

    def test_bad_requests(self):
        self.assertEqual(self.post("/tx/", {"writes": []}).status_code, 400)
        self.assertEqual(self.post("/tx/", {}).status_code, 400)
        self.assertEqual(self.tx({"op": "delete", "collection": "workflows", "id": "wf-1"}).status_code, 400)
        self.assertEqual(self.tx({"op": "insert", "collection": "nope", "id": "x", "doc": {}}).status_code, 400)
        self.assertEqual(self.tx({"op": "insert", "collection": "workflows", "id": "x", "doc": {"id": "x"}}).status_code, 400)
        self.assertEqual(self.tx({"op": "insert", "collection": "workflows", "id": "x", "doc": "text"}).status_code, 400)
        self.assertEqual(self.get("/c/nope/").status_code, 404)
        self.assertEqual(self.get("/c/workflows/?nope=1").status_code, 400)

    def test_there_is_no_delete_or_put_anywhere(self):
        client = Client()
        headers = {"HTTP_HOST": HOST_A, "HTTP_X_LEVV_STORE_TOKEN": TOKEN}
        for path in ("/c/workflows/wf-1/", "/c/workflows/", "/tx/"):
            self.assertEqual(client.delete(f"{BASE}{path}", **headers).status_code, 405, path)
            self.assertEqual(client.put(f"{BASE}{path}", **headers).status_code, 405, path)
        self.assertEqual(self.get("/c/workflows/wf-1/").status_code, 200)

    def test_a_bad_timestamp_is_refused(self):
        doc = self.run_doc()
        doc["startedAt"] = "not a date"
        self.assertEqual(self.tx({"op": "insert", "collection": "runs", "id": "run-1", "doc": doc}).status_code, 400)

    def test_columns_follow_the_doc(self):
        self.tx({"op": "patch", "collection": "workflows", "id": "wf-1", "set": {"status": "live", "version": 3, "priority": 2}})
        connection.set_tenant(self.tenant_a)
        row = Workflow.objects.get(pk="wf-1")
        self.assertEqual((row.status, row.version, row.priority, row.record_type, row.source), ("live", 3, 2, "work_order", "levv"))


class SeedTargetTests(StoreTestCase):
    def test_it_reports_the_schema_and_whether_the_database_is_local(self):
        with self.settings(DATABASES={"default": {"HOST": "127.0.0.1"}}):
            self.assertEqual(self.get("/seed-target/").json(), {"schema": "flow_a", "databaseIsLocal": True})
        with self.settings(DATABASES={"default": {"HOST": "db.example.supabase.co"}}):
            self.assertEqual(self.get("/seed-target/").json()["databaseIsLocal"], False)


class RunClaimTests(StoreTestCase):
    """05 section 3.1 step 4 made atomic: one claim row, so one of two identical events starts a run."""

    def test_a_second_insert_of_one_claim_conflicts(self):
        write = {"op": "insert", "collection": "claims", "id": "wf:1:WO-1:submitted", "doc": {"runId": "run-a", "startedAt": "2026-10-20T09:00:00Z"}}
        self.assertEqual(self.tx(write).status_code, 200)
        second = self.tx(dict(write, doc={"runId": "run-b", "startedAt": "2026-10-20T09:00:00Z"}))
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["reason"], "exists")

    def test_a_claim_moves_only_from_the_run_it_was_read_with(self):
        self.tx({"op": "insert", "collection": "claims", "id": "c", "doc": {"runId": "run-a", "startedAt": "x"}})
        move = {"op": "patch", "collection": "claims", "id": "c", "set": {"runId": "run-b"}, "expect": {"runId": "run-a"}}
        self.assertEqual(self.tx(move).status_code, 200)
        stale = self.tx(move)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["reason"], "expect")
