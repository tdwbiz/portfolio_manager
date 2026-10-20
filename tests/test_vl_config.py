"""Tests for VL client configuration (vl-halogen plan).

Step 2: ``VLClient._diag_connection`` must tolerate both ``/models`` response
shapes and never raise. Step 3: the three VL entry points must construct
``VLClient`` from the settings values. All mocks — no network access.
"""

from unittest.mock import MagicMock, patch

from django.conf import settings
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


class ParsersUseSettingsTest(TestCase):
    """vl-halogen plan, step 3, test 1.

    Each VL entry point must construct ``VLClient`` with the settings values:
    ``model``, ``base_url``, ``api_key``, ``max_tokens`` and ``temperature``
    all come from ``settings.VL_MODEL_*`` / ``VL_MAX_TOKENS`` /
    ``VL_TEMPERATURE``. ``VLClient`` is patched in each module — no network.
    """

    def _expected_kwargs(self):
        return dict(
            base_url=settings.VL_MODEL_BASE_URL,
            model=settings.VL_MODEL_NAME,
            api_key=settings.VL_MODEL_API_KEY,
            timeout=settings.VL_TIMEOUT_SECONDS,
            max_retries=settings.VL_MAX_RETRIES,
            max_tokens=settings.VL_MAX_TOKENS,
            temperature=settings.VL_TEMPERATURE,
        )

    @patch("imports.parsers_monthly_vl._render_pdf_pages")
    @patch("imports.parsers_monthly_vl.VLClient")
    def test_monthly_uses_settings(self, mock_client_cls, mock_render):
        """parse_rbc_monthly_vl constructs VLClient with the settings values."""
        mock_render.return_value = ["fake_image_b64"]
        mock_client = MagicMock()
        mock_client.extract_json.return_value = {
            "account_number": "12345-678901",
            "statement_date": "2024-05-31",
            "last_statement_date": "2024-04-30",
            "fx_rate": 1.35,
            "sections": [
                {
                    "currency": "CAD",
                    "begin_total_market_value": 100.00,
                    "end_total_market_value": 200.00,
                    "balances": [],
                    "transactions": [],
                },
            ],
        }
        mock_client_cls.return_value = mock_client

        from imports.parsers_monthly_vl import parse_rbc_monthly_vl

        result = parse_rbc_monthly_vl("/fake/monthly.pdf")
        self.assertEqual(len(result), 1)

        mock_client_cls.assert_called_once_with(**self._expected_kwargs())

    @patch("imports.parsers_annual_vl._render_pdf_pages")
    @patch("imports.parsers_annual_vl.VLClient")
    def test_annual_uses_settings(self, mock_client_cls, mock_render):
        """parse_rbc_annual_report_vl constructs VLClient with the settings values."""
        mock_render.return_value = ["fake_image_b64"]
        mock_client = MagicMock()
        mock_client.extract_json.return_value = {
            "account_number": "12345-678901",
            "account_type_code": "TFSA",
            "report_period_start": "2024-01-01",
            "report_period_end": "2024-12-31",
            "report_year": 2024,
            "cad": {"bal_beginning_value": 100.00, "bal_ending_value": 200.00},
            "usd": None,
        }
        mock_client_cls.return_value = mock_client

        from imports.parsers_annual_vl import parse_rbc_annual_report_vl

        result = parse_rbc_annual_report_vl("/fake/annual.pdf")
        self.assertEqual(result["report_year"], 2024)

        mock_client_cls.assert_called_once_with(**self._expected_kwargs())

    @patch("imports.classify_pdf_vl._render_first_page")
    @patch("imports.classify_pdf_vl.VLClient")
    def test_classify_uses_settings(self, mock_client_cls, mock_render):
        """classify_pdf constructs VLClient with the settings values.

        The filename must not match the fast-path heuristics so the VL model
        is actually called.
        """
        mock_render.return_value = ["fake_image_b64"]
        mock_client = MagicMock()
        mock_client.extract_json.return_value = {"doc_type": "monthly"}
        mock_client_cls.return_value = mock_client

        from imports.classify_pdf_vl import classify_pdf

        result = classify_pdf("/fake/rbc_document.pdf")
        self.assertEqual(result, "monthly")

        mock_client_cls.assert_called_once_with(**self._expected_kwargs())
