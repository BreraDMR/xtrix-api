"""Any OpenAI-compatible chat completions API: OpenRouter, Google AI Studio, Groq, Cerebras,
Mistral, Cohere, NVIDIA, Cloudflare, Ollama..."""
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.ai.providers.base import Completion, Message, ProviderError, as_int, post_json


class OpenAICompatAdapter:
    def __init__(self, base_url: str, api_key: Optional[str], *, json_mode: str = "json_object",
                 http: Optional[httpx.Client] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.json_mode = json_mode
        self.http = http or httpx.Client()
        self.is_openrouter = (urlparse(self.base_url).hostname or "").endswith("openrouter.ai")

    def build_body(self, messages: list[Message], *, model: str, json_schema: Optional[dict], max_tokens: int) -> dict:
        body = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
        }
        if self.json_mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif self.json_mode == "json_schema" and json_schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "plan", "schema": json_schema, "strict": False}}
        if self.is_openrouter:
            # only route to providers that don't keep or train on what we send
            body["provider"] = {"data_collection": "deny"}
        return body

    def complete(self, messages: list[Message], *, model: str, json_schema: Optional[dict],
                 max_tokens: int, timeout: float) -> Completion:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = post_json(self.http, f"{self.base_url}/chat/completions", headers=headers,
                         body=self.build_body(messages, model=model, json_schema=json_schema, max_tokens=max_tokens),
                         timeout=timeout)
        if data.get("error"):
            # OpenRouter and a few others can answer 200 with an error object inside
            raise ProviderError("bad_response", detail="error object in a 200 answer")
        try:
            content = data["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("bad_response", detail="no choices[0].message") from None
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("bad_response", detail="empty content")
        usage = data.get("usage") or {}
        return Completion(content, as_int(usage.get("prompt_tokens")), as_int(usage.get("completion_tokens")))
