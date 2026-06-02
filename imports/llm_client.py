"""Thin OpenAI-compatible client for the Qwen2.5-VL vision-language model.

Sends one or more PDF pages (as base64 PNG images) and a structured prompt to
the llama.cpp server on port 11440, then extracts the JSON response.

Hard error on failure — no fallback to regex.
"""
import base64
import json
import logging
import re
import time

from openai import OpenAI

logger = logging.getLogger(__name__)


class VLParseError(Exception):
    """Raised when the VL model fails to return valid JSON after all retries."""


class VLClient:
    """Client for the Qwen2.5-VL vision-language model on port 11440."""

    def __init__(self, base_url: str, model: str, timeout: int = 120, max_retries: int = 2):
        self._client = OpenAI(base_url=base_url, api_key='not-used', timeout=timeout)
        self._model = model
        self._max_retries = max_retries

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

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                logger.debug("VLClient: sending %d page(s) to %s (attempt %d/%d)",
                             len(pages), self._model, attempt, self._max_retries)
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": content}],
                    temperature=0.1,
                    max_tokens=8192,
                )
                text = response.choices[0].message.content or ''
                logger.debug("VLClient raw response: %s", text[:500])

                # Extract JSON from the response — model may wrap it in ```json ... ```
                parsed = self._extract_json_from_text(text)
                logger.info("VLClient: parsed JSON successfully on attempt %d", attempt)
                return parsed

            except (VLParseError, json.JSONDecodeError) as exc:
                last_error = exc
                logger.warning("VLClient: attempt %d failed — %s", attempt, exc)
                if attempt < self._max_retries:
                    time.sleep(2)
            except Exception as exc:
                last_error = exc
                logger.error("VLClient: HTTP/transport error on attempt %d — %s", attempt, exc)
                if attempt < self._max_retries:
                    time.sleep(2)

        raise VLParseError(
            f"VL model failed to return valid JSON after {self._max_retries} attempt(s). "
            f"Last error: {last_error}"
        )

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

        return json.loads(text)
