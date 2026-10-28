"""Thin OpenAI-compatible client for a vision-language (VL) model.

Sends one or more PDF pages (as base64 PNG images) and a structured prompt to
any OpenAI-compatible vision endpoint (default: Halogen on port 11435), then
extracts the JSON response.

Hard error on failure — no fallback to regex.
"""
import json
import logging
import re
import time
from typing import Any

import httpx
from openai import APIConnectionError, APITimeoutError, OpenAI

logger = logging.getLogger(__name__)


class VLParseError(Exception):
    """Raised when the VL model fails to return valid JSON after all retries."""


class VLClient:
    """Client for an OpenAI-compatible vision-language model (default: Halogen)."""

    def __init__(self, base_url: str, model: str, api_key: str = 'not-used',
                 timeout: int = 120, max_retries: int = 2,
                 max_tokens: int = 8192, temperature: float = 0.1):
        # Disable OpenAI library's built-in retries — we handle retries ourselves with full logging
        self._httpx_client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=30),
            limits=httpx.Limits(max_connections=1, keepalive_expiry=5),
        )
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            http_client=self._httpx_client,
            max_retries=0,  # We manage retries ourselves
        )
        self._model = model
        self._max_retries = max_retries
        self._base_url = base_url
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._temperature = temperature
        # Log connection info on init
        logger.info("VLClient: connecting to %s model=%s timeout=%ds retries=%d "
                     "max_tokens=%d temperature=%s",
                     base_url, model, timeout, max_retries, max_tokens, temperature)
        self._diag_connection(base_url)

    def extract_json(self, pages: list[str], prompt: str) -> dict:
        """Send pages + prompt to the VL model and return parsed JSON.

        Parameters
        ----------
        pages:
            List of base64-encoded PNG strings, one per PDF page.
        prompt:
            System instruction that describes the expected JSON schema.

        Returns
        -------
        dict
            The parsed JSON payload returned by the model.

        Raises
        ------
        VLParseError
            If the model fails to return valid JSON after max_retries attempts.
        """
        # Build content: text prompt followed by one image per page
        content: list[dict] = [{"type": "text", "text": prompt}]
        for page_b64 in pages:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{page_b64}",
                    "detail": "high",
                },
            })

        # Log payload size for diagnostics
        total_b64_size = sum(len(p) for p in pages)
        prompt_len = len(prompt)
        logger.info("VLClient: payload = %d pages, prompt=%d chars, images=%d KB total",
                     len(pages), prompt_len, total_b64_size // 1024)

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            t0 = time.monotonic()
            try:
                logger.info("VLClient: attempt %d/%d starting", attempt, self._max_retries)
                kwargs: dict[str, Any] = {
                    "model": self._model,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": self._temperature,
                    "max_tokens": self._max_tokens,
                }
                response = self._client.chat.completions.create(**kwargs)
                elapsed = time.monotonic() - t0
                choice = response.choices[0]
                if choice.finish_reason == 'length':
                    # A truncated answer is unusable: raise so the attempt is
                    # retried and the final VLParseError names the truncation.
                    raise VLParseError(
                        f"VL model output truncated "
                        f"(finish_reason=length, max_tokens={self._max_tokens})"
                    )
                text = choice.message.content or ''
                logger.info("VLClient: attempt %d got response in %.1fs, content=%d chars",
                            attempt, elapsed, len(text))

                # Check for empty response
                if not text.strip():
                    raise VLParseError(
                        f"VL model returned empty response (attempt {attempt})"
                    )

                # Log first/last 200 chars for debugging
                if len(text) > 400:
                    logger.debug("VLClient raw response start: %s", text[:200])
                    logger.debug("VLClient raw response end: %s", text[-200:])
                else:
                    logger.debug("VLClient raw response: %s", text)

                # Extract JSON from the response — model may wrap it in ```json ... ```
                parsed = self._extract_json_from_text(text)
                logger.info("VLClient: parsed JSON successfully on attempt %d (%.1fs total)",
                            attempt, time.monotonic() - t0)
                return parsed

            except APITimeoutError as exc:
                elapsed = time.monotonic() - t0
                last_error = exc
                logger.error("VLClient: attempt %d timeout after %.1fs — %s",
                             attempt, elapsed, exc)
                if attempt < self._max_retries:
                    wait = 5
                    logger.info("VLClient: waiting %ds before retry", wait)
                    time.sleep(wait)

            except APIConnectionError as exc:
                elapsed = time.monotonic() - t0
                last_error = exc
                logger.error("VLClient: attempt %d APIConnectionError after %.1fs — %s",
                             attempt, elapsed, exc)
                # Log extra detail about the error
                if hasattr(exc, 'request') and exc.request:
                    logger.error("VLClient: request method=%s url=%s",
                                 exc.request.method, exc.request.url)
                if hasattr(exc, 'code'):
                    logger.error("VLClient: HTTP status code=%s", exc.code)
                if attempt < self._max_retries:
                    wait = 5
                    logger.info("VLClient: waiting %ds before retry", wait)
                    time.sleep(wait)

            except (VLParseError, json.JSONDecodeError) as exc:
                elapsed = time.monotonic() - t0
                last_error = exc
                logger.warning("VLClient: attempt %d parse error after %.1fs — %s",
                               attempt, elapsed, exc)
                if attempt < self._max_retries:
                    wait = 2
                    logger.info("VLClient: waiting %ds before retry", wait)
                    time.sleep(wait)

            except Exception as exc:
                elapsed = time.monotonic() - t0
                last_error = exc
                logger.error("VLClient: attempt %d unexpected error after %.1fs — %s: %s",
                             attempt, elapsed, type(exc).__name__, exc)
                if attempt < self._max_retries:
                    wait = 5
                    logger.info("VLClient: waiting %ds before retry", wait)
                    time.sleep(wait)

        raise VLParseError(
            f"VL model failed to return valid JSON after {self._max_retries} attempt(s). "
            f"Last error: {last_error}"
        )

    @staticmethod
    def _diag_connection(base_url: str):
        """Quick connectivity diagnostic on init.

        Tolerates both ``/models`` response shapes — llama.cpp
        ``{"models": [{"name": ...}]}`` and OpenAI-compatible (Halogen)
        ``{"data": [{"id": ...}]}`` — plus any unexpected body. This is a
        log line only: it must never raise, so a failed or unparseable
        connection test cannot prevent the client from being created.
        """
        try:
            # Try to reach /v1/models endpoint
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{base_url}/models")
                body = resp.json()
                model_ids: list[str] = []
                if isinstance(body, dict):
                    for m in body.get('models') or []:
                        if isinstance(m, dict):
                            model_ids.append(str(m.get('name') or m.get('id')))
                    if not model_ids:
                        for m in body.get('data') or []:
                            if isinstance(m, dict):
                                model_ids.append(str(m.get('id') or m.get('name')))
                logger.info("VLClient: connection test OK — status=%d models=%s",
                            resp.status_code, model_ids)
        except Exception as exc:
            logger.warning("VLClient: connection test FAILED (%s: %s)",
                           type(exc).__name__, exc)

    @staticmethod
    def _extract_json_from_text(text: str) -> dict:
        """Extract a JSON object from a model response that may include markdown fences."""
        # Strip markdown code fences: ```json ... ``` or ``` ... ```
        fenced = re.search(r'```(?:json)?\s*([\s\S]*?)```', text, re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        else:
            # Find first { ... } block
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1 and end > start:
                text = text[start:end + 1]

        # Fix common JSON errors from LLM output
        # Remove trailing commas before } or ]
        text = re.sub(r',\s*([\]}])', r'\1', text)

        return json.loads(text)
