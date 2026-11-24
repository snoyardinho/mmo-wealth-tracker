"""Read a screenshot with a Claude vision model into raw cells, following the adapter."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from .adapter import Adapter, Screen

MODEL = "claude-opus-5-5"
# USD per million tokens, for the cost estimate printed by the golden run.
PRICE_PER_MTOK = {"claude-opus-5-5": (4.00, 20.00)}

SYSTEM = """You copy text out of screenshots of video game market screens.
Copy each cell exactly as it is displayed: same digits, commas, decimal points, minus signs and spelling.
Never calculate, round, correct or guess a value. If a cell cannot be read, return an empty string for it.
Keep rows in the order they appear, top to bottom. Do not include header rows, filter controls or pagers."""

MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class ExtractionError(RuntimeError):
    pass


@dataclass
class CallStats:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    models: list[str] = field(default_factory=list)

    def add(self, message: Any, seconds: float) -> None:
        self.calls += 1
        self.input_tokens += message.usage.input_tokens
        self.output_tokens += message.usage.output_tokens
        self.seconds += seconds
        self.models.append(message.model)

    def cost_usd(self, model: str = MODEL) -> float | None:
        price = PRICE_PER_MTOK.get(model)
        if price is None:
            return None
        return (self.input_tokens * price[0] + self.output_tokens * price[1]) / 1_000_000


def image_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _image_block(path: Path) -> dict[str, Any]:
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        # Uploads may arrive without an extension; sniff the PNG/JPEG header.
        head = path.read_bytes()[:8]
        media_type = "image/png" if head.startswith(b"\x89PNG") else "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


class Extractor:
    def __init__(self, adapter: Adapter, model: str = MODEL, effort: str = "medium",
                 client: anthropic.Anthropic | None = None):
        self.adapter = adapter
        self.model = model
        self.effort = effort
        self.client = client or anthropic.Anthropic()
        self.stats = CallStats()

    def _ask(self, image: dict[str, Any], prompt: str, schema: dict[str, Any], effort: str) -> dict[str, Any]:
        started = time.monotonic()
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=64000,
            system=SYSTEM,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": [image, {"type": "text", "text": prompt}]}],
        ) as stream:
            message = stream.get_final_message()
        self.stats.add(message, time.monotonic() - started)
        if message.stop_reason == "refusal":
            raise ExtractionError("the model declined to read this screenshot")
        if message.stop_reason == "max_tokens":
            raise ExtractionError("the reply was cut off; the screenshot may hold too many rows")
        text = next((b.text for b in message.content if b.type == "text"), None)
        if text is None:
            raise ExtractionError("no text in the reply")
        return json.loads(text)

    def classify(self, path: Path) -> str:
        answer = self._ask(_image_block(path), self.adapter.classify_instructions(),
                           self.adapter.classify_schema(), effort="low")
        return answer["screen"]

    def extract(self, path: Path, screen: Screen | None = None) -> tuple[Screen, dict[str, Any]]:
        image = _image_block(path)
        if screen is None:
            name = self.classify(path)
            if name == "unknown":
                raise ExtractionError(f"{path.name} does not look like a known {self.adapter.name} screen")
            screen = self.adapter.screen(name)
        prompt = f"This is a {self.adapter.name} screenshot.\n\n{screen.instructions()}"
        raw = self._ask(image, prompt, screen.raw_schema(), effort=self.effort)
        return screen, raw
