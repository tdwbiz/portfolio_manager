"""Tests for VL client configuration (vl-halogen plan, step 2).

Covers ``VLClient._diag_connection``: it must tolerate both ``/models``
response shapes and never raise. httpx is mocked — no network access.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from imports.llm_client import VLClient


def _mock_get_response(body):
    """Build a mock httpx.Response whose .json() returns *body*."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = body
    return resp


class DiagConnectionTest(TestCase):
    """VLClient._diag_connection must never raise, whatever the server returns."""

    def _run_diag(self, response_body):
        with patch("imports.llm_client.httpx.Client") as mock_client_cls:
            client = mock_client_cls.return_value.__enter__.return_value
            client.get.return_value = _mock_get_response(response_body)
            # Must not raise.
            VLClient._diag_connection("http://127.0.0.1:11435/v1")
        client.get.assert_called_once_with("http://127.0.0.1:11435/v1/models")

    def test_openai_shape_data_id(self):
        """OpenAI-compatible shape: {"data": [{"id": ...}]} (Halogen)."""
        self._run_diag({"data": [{"id": "halogen-qwen3.8-flash-next"}]})

    def test_llamacpp_shape_models_name(self):
        """llama.cpp shape: {"models": [{"name": ...}]}."""
        self._run_diag({"models": [{"name": "qwen2.5-vl"}]})

    def test_garbage_body(self):
        """Unexpected JSON body — no exception, empty model list logged."""
        self._run_diag({"unexpected_key": [1, 2, 3]})

    def test_non_dict_body(self):
        """JSON that is not an object (e.g. a bare list) — no exception."""
        self._run_diag(["not", "a", "dict"])

    def test_json_decode_failure(self):
        """resp.json() raising — no exception escapes _diag_connection."""
        with patch("imports.llm_client.httpx.Client") as mock_client_cls:
            client = mock_client_cls.return_value.__enter__.return_value
            resp = MagicMock()
            resp.status_code = 200
            resp.json.side_effect = ValueError("not JSON")
            client.get.return_value = resp
            # Must not raise.
            VLClient._diag_connection("http://127.0.0.1:11435/v1")

    def test_connection_failure(self):
        """The GET itself failing (no server) — no exception escapes."""
        with patch("imports.llm_client.httpx.Client") as mock_client_cls:
            client = mock_client_cls.return_value.__enter__.return_value
            client.get.side_effect = ConnectionError("connection refused")
            # Must not raise.
            VLClient._diag_connection("http://127.0.0.1:11435/v1")
