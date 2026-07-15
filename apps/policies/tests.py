from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.policies.models import WorkflowBlock
from apps.policies.serializers import WorkerLifecycleWorkflowSerializer


class FakeRelatedManager:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class WorkerLifecycleWorkflowSerializerTests(SimpleTestCase):
    def workflow_graph_payload(self):
        return {
            "name": "Graph round trip",
            "workflow_type": "onboarding",
            "dependencies": [
                {"from_block_key": "__start__", "to_block_key": "collect-docs"},
                {"from_block_key": "collect-docs", "to_block_key": "activate-worker"},
                {"from_block_key": "activate-worker", "to_block_key": "__end__"},
            ],
            "blocks": [
                {
                    "sequence": 1,
                    "client_key": "collect-docs",
                    "block_type": WorkflowBlock.TYPE_REQUIREMENT,
                    "name": "Collect documents",
                    "gate_type": WorkflowBlock.GATE_HARD,
                    "config": {
                        "workflow_graph": {
                            "incoming": ["__start__"],
                            "outgoing": ["activate-worker"],
                        },
                    },
                    "layout": {
                        "level": 1,
                        "position": 0,
                        "workflow_graph": {
                            "incoming": ["__start__"],
                            "outgoing": ["activate-worker"],
                        },
                    },
                },
                {
                    "sequence": 2,
                    "client_key": "activate-worker",
                    "block_type": WorkflowBlock.TYPE_SYSTEM,
                    "name": "Active",
                    "gate_type": WorkflowBlock.GATE_SOFT,
                    "integration_type": WorkflowBlock.INTEGRATION_API_CALL,
                    "config": {
                        "workflow_graph": {
                            "incoming": ["collect-docs"],
                            "outgoing": ["__end__"],
                        },
                    },
                    "layout": {
                        "level": 2,
                        "position": 0,
                        "workflow_graph": {
                            "incoming": ["collect-docs"],
                            "outgoing": ["__end__"],
                        },
                    },
                },
            ],
        }

    def test_accepts_synthetic_dependencies_and_preserves_graph_json(self):
        payload = self.workflow_graph_payload()

        serializer = WorkerLifecycleWorkflowSerializer(data=payload)

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(payload["dependencies"], serializer.validated_data["dependencies"])
        self.assertEqual("collect-docs", serializer.validated_data["blocks"][0]["client_key"])
        self.assertEqual(
            payload["blocks"][0]["config"]["workflow_graph"],
            serializer.validated_data["blocks"][0]["config"]["workflow_graph"],
        )
        self.assertEqual(
            payload["blocks"][0]["layout"]["workflow_graph"],
            serializer.validated_data["blocks"][0]["layout"]["workflow_graph"],
        )

    def test_rejects_unknown_dependency_references(self):
        payload = self.workflow_graph_payload()
        payload["dependencies"] = [
            {"from_block_key": "__start__", "to_block_key": "missing-block"},
        ]

        serializer = WorkerLifecycleWorkflowSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("dependencies", serializer.errors)

    def test_rejects_circular_dependency_graphs(self):
        payload = self.workflow_graph_payload()
        payload["dependencies"] = [
            {"from_block_key": "collect-docs", "to_block_key": "activate-worker"},
            {"from_block_key": "activate-worker", "to_block_key": "collect-docs"},
        ]

        serializer = WorkerLifecycleWorkflowSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("dependencies", serializer.errors)

    def test_health_reports_dependency_cycles(self):
        workflow = SimpleNamespace(
            name="Cyclic workflow",
            dependencies=[
                {"from_block_key": "first", "to_block_key": "second"},
                {"from_block_key": "second", "to_block_key": "first"},
            ],
            blocks=FakeRelatedManager(
                [
                    SimpleNamespace(
                        id=1,
                        block_type=WorkflowBlock.TYPE_SYSTEM,
                        gate_type=WorkflowBlock.GATE_SOFT,
                        integration_type=WorkflowBlock.INTEGRATION_API_CALL,
                        requirements=FakeRelatedManager([]),
                    )
                ]
            ),
        )

        health = WorkerLifecycleWorkflowSerializer().get_health(workflow)

        self.assertFalse(health["checks"]["no_circular_dependencies"])
