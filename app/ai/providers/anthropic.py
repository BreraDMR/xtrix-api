"""Anthropic Messages API, called directly (no OpenAI shim in between)."""
from typing import Optional

import httpx

from app.ai.providers.base import Completion, Message, ProviderError, as_int, post_json

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAdapter:
    def __init__(self, api_key: str, *, base_url: str = "https://api.anthropic.com",
                 http: Optional[httpx.Client] = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client()

    @staticmethod
    def build_body(messages: list[Message], *, model: str, max_tokens: int) -> dict:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns: list[dict] = []
        for m in messages:
            if m.role == "system":
                continue
            if turns and turns[-1]["role"] == m.role:
                # the API wants user/assistant turns to alternate
                turns[-1]["content"] += "\n\n" + m.content
            else:
                turns.append({"role": m.role, "content": m.content})
        body = {"model": model, "max_tokens": max_tokens, "messages": turns}
        if system:
            body["system"] = system
        return body

    def complete(self, messages: list[Message], *, model: str, json_schema: Optional[dict],
                 max_tokens: int, timeout: float) -> Completion:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        data = post_json(self.http, f"{self.base_url}/v1/messages", headers=headers,
                         body=self.build_body(messages, model=model, max_tokens=max_tokens), timeout=timeout)
        if data.get("stop_reason") == "refusal":
            raise ProviderError("bad_response", detail="refusal")
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        if not text.strip():
            raise ProviderError("bad_response", detail="no text block")
        usage = data.get("usage") or {}
        return Completion(text, as_int(usage.get("input_tokens")), as_int(usage.get("output_tokens")))
