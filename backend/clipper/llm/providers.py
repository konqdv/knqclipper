"""LLM providers returning JSON: local Ollama, any OpenAI-compatible endpoint, or Anthropic.

All providers implement ``complete_json(system, user, images)``. Images (JPEG bytes) are only sent
when the provider is configured as vision-capable.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise LLMError("no JSON object in model output")
        return json.loads(m.group(0))


class LLMProvider:
    name = "none"

    def __init__(self, model: str, base_url: str | None, api_key: str | None, vision: bool, timeout: float) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.vision = vision
        self.timeout = timeout
        self._sem = asyncio.Semaphore(2)
        self.calls = 0
        self.failures = 0

    async def complete_json(self, system: str, user: str, images: list[bytes] | None = None) -> dict[str, Any]:
        async with self._sem:
            self.calls += 1
            try:
                return await self._complete(system, user, images if self.vision else None)
            except Exception as exc:
                self.failures += 1
                raise LLMError(str(exc)) from exc

    async def _complete(self, system: str, user: str, images: list[bytes] | None) -> dict[str, Any]:
        raise NotImplementedError

    async def health(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model}


class OllamaProvider(LLMProvider):
    name = "ollama"

    async def _complete(self, system, user, images):
        base = (self.base_url or "http://localhost:11434").rstrip("/")
        msg: dict[str, Any] = {"role": "user", "content": user}
        if images:
            msg["images"] = [base64.b64encode(i).decode() for i in images]
        body = {"model": self.model, "stream": False, "format": "json", "options": {"temperature": 0.2},
                "messages": [{"role": "system", "content": system}, msg]}
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{base}/api/chat", json=body)
            r.raise_for_status()
            return extract_json(r.json()["message"]["content"])

    async def health(self):
        base = (self.base_url or "http://localhost:11434").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{base}/api/tags")
                models = [m["name"] for m in r.json().get("models", [])]
            return {"provider": self.name, "model": self.model, "ok": True, "available": self.model in models, "models": models[:20]}
        except Exception as exc:  # noqa: BLE001
            return {"provider": self.name, "model": self.model, "ok": False, "error": str(exc)}


class OpenAICompatibleProvider(LLMProvider):
    name = "openai"

    async def _complete(self, system, user, images):
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(i).decode()}} for i in images]
        body = {"model": self.model, "temperature": 0.2, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{base}/chat/completions", json=body, headers=headers)
            if r.status_code == 400 and "response_format" in r.text:
                body.pop("response_format")
                r = await c.post(f"{base}/chat/completions", json=body, headers=headers)
            r.raise_for_status()
            return extract_json(r.json()["choices"][0]["message"]["content"])


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    async def _complete(self, system, user, images):
        base = (self.base_url or "https://api.anthropic.com").rstrip("/")
        content: list[dict[str, Any]] = []
        for i in images or []:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(i).decode()}})
        content.append({"type": "text", "text": user + "\n\nRespond with a single JSON object only."})
        body = {"model": self.model, "max_tokens": 1500, "temperature": 0.2, "system": system,
                "messages": [{"role": "user", "content": content}]}
        headers = {"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01", "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{base}/v1/messages", json=body, headers=headers)
            r.raise_for_status()
            js = r.json()
            text = "".join(b.get("text", "") for b in js.get("content", []) if b.get("type") == "text")
            return extract_json(text)


def make_provider(settings) -> LLMProvider | None:
    kind = settings.llm_provider
    if kind == "none":
        return None
    cls = {"ollama": OllamaProvider, "openai": OpenAICompatibleProvider, "anthropic": AnthropicProvider}[kind]
    return cls(settings.llm_model, settings.llm_base_url, settings.llm_api_key, settings.llm_vision, settings.llm_timeout_s)
