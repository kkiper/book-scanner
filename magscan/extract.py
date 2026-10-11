"""Read a magazine cover with a vision model and return structured metadata."""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request

import anthropic
from pydantic import ValidationError

from .config import Settings
from .cover import CoverData
from .metadata import MagazineInfo

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You catalogue a digital magazine collection. You are given the front cover of one \
magazine issue, plus (when available) the text layer of its first pages, its current file name and any \
metadata embedded in the file. Identify the issue and return its details.

Rules:
- The cover is the primary source. Use the text excerpt, file name and embedded metadata to confirm or \
fill in details the cover doesn't show (issue number, publisher), never to override what the cover clearly says.
- Use the masthead name as branded ("PC Gamer", not "PC GAMER" and not "PC Gamer Magazine"), in normal title case \
unless the brand's own styling is lowercase or a known acronym.
- Leave a field null rather than guessing. Do not invent issue numbers or dates.
- Cover lines: short teaser texts as printed, cleaned of line breaks, without prices or barcodes.
- confidence reflects how sure you are about the magazine name and cover date together."""


def _context_text(cover: CoverData, filename: str) -> str:
    parts = [f"Current file name: {filename}"]
    if cover.embedded:
        parts.append("Embedded file metadata: " + json.dumps(cover.embedded, ensure_ascii=False))
    for i, text in enumerate(cover.page_text, start=1):
        if text:
            parts.append(f"Text layer of page {i} (excerpt, may be empty for scans):\n{text}")
    if not any(cover.page_text):
        parts.append("The file has no text layer; rely on the cover image.")
    parts.append("Identify this magazine issue from the cover image above.")
    return "\n\n".join(parts)


def _schema() -> dict:
    """Pydantic's JSON schema, inlined into the shape structured outputs expects."""
    schema = MagazineInfo.model_json_schema()
    schema.pop("title", None)
    schema["additionalProperties"] = False
    schema["required"] = list(schema["properties"].keys())
    for prop in schema["properties"].values():
        prop.pop("default", None)
        prop.pop("title", None)
    return schema


class ExtractionError(Exception):
    pass


class ClaudeExtractor:
    def __init__(self, settings: Settings):
        self.model = settings.claude_model
        self.client = anthropic.Anthropic(max_retries=4)

    def extract(self, cover: CoverData, filename: str) -> MagazineInfo:
        if not (self.client.api_key or self.client.auth_token):
            raise ExtractionError("No Anthropic API key: set ANTHROPIC_API_KEY on the container, or switch the provider to Ollama")
        image_b64 = base64.standard_b64encode(cover.jpeg).decode()
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            # Reading a cover is an extraction task; low effort keeps it quick and cheap.
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _schema()}},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                    {"type": "text", "text": _context_text(cover, filename)},
                ],
            }],
        )
        try:
            if self.model in ("claude-opus-5-5", "claude-fable-5-1", "claude-opus-5", "claude-sonnet-5-5"):
                # If a safety classifier declines (false positives happen on some covers),
                # let the API retry on a suitable fallback model inside the same call.
                response = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                response = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise ExtractionError("Anthropic API key is missing or invalid (set ANTHROPIC_API_KEY)") from e
        except anthropic.NotFoundError as e:
            raise ExtractionError(f"Unknown Claude model '{self.model}'") from e
        except anthropic.RateLimitError as e:
            raise ExtractionError("Rate limited by the Anthropic API after retries; try again later") from e
        except anthropic.APIStatusError as e:
            raise ExtractionError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ExtractionError(f"Could not reach the Anthropic API: {e}") from e

        if response.stop_reason == "refusal":
            raise ExtractionError("The model declined to read this cover")
        if response.stop_reason == "max_tokens":
            raise ExtractionError("The model's answer was cut off")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise ExtractionError("The model returned no answer")
        try:
            return MagazineInfo.model_validate_json(text)
        except ValidationError as e:
            raise ExtractionError(f"Unexpected answer from the model: {e}") from e


class OllamaExtractor:
    """Local alternative for people who'd rather not send covers to an API.

    Needs a vision model pulled into Ollama, e.g. ``ollama pull qwen2.5vl:7b``.
    Expect noticeably lower accuracy than Claude on busy covers.
    """

    def __init__(self, settings: Settings):
        self.url = settings.ollama_url.rstrip("/") + "/api/chat"
        self.model = settings.ollama_model

    def extract(self, cover: CoverData, filename: str) -> MagazineInfo:
        body = {
            "model": self.model,
            "stream": False,
            "format": _schema(),
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _context_text(cover, filename),
                 "images": [base64.standard_b64encode(cover.jpeg).decode()]},
            ],
        }
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:
            raise ExtractionError(f"Ollama error {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ExtractionError(f"Could not reach Ollama at {self.url}: {e}") from e
        try:
            return MagazineInfo.model_validate_json(payload["message"]["content"])
        except (KeyError, ValidationError) as e:
            raise ExtractionError(f"Unexpected answer from Ollama: {e}") from e


def get_extractor(settings: Settings):
    if settings.provider == "ollama":
        return OllamaExtractor(settings)
    return ClaudeExtractor(settings)
