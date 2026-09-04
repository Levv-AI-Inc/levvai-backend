from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import resolve
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.intake.views import NovaChatView


class NovaChatViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = SimpleNamespace(id=42, schema_name="tenant")
    def _request(self, data):
        request = self.factory.post("/api/nova/chat", data, format="json")
        request.tenant = self.tenant
        request.session = {}
        return request

    def test_api_nova_chat_url_resolves(self):
        match = resolve("/api/nova/chat")

        self.assertEqual("api-nova-chat", match.url_name)

    def test_requires_messages(self):
        response = NovaChatView.as_view()(self._request({"messages": []}))

        self.assertEqual(status.HTTP_400_BAD_REQUEST, response.status_code)
        self.assertIn("messages", response.data)

    def test_returns_reply_payload_without_authentication(self):
        with patch("apps.intake.views._call_nova_chat", return_value="Hey Faraz - what can I help you with?") as chat:
            response = NovaChatView.as_view()(
                self._request(
                    {
                        "messages": [{"role": "user", "content": "hi"}],
                        "policyActive": False,
                    }
                )
            )

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertEqual({"reply": "Hey Faraz - what can I help you with?"}, response.data)
        chat.assert_called_once()
