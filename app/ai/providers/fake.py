"""LLM_PROVIDER=fake: the "model" answers with the contents of a JSON file. For web E2E runs and demos,
so nobody needs a key and nothing leaves the machine. The answer goes through the same parsing and
schema check as a real model's. Refuses to start in production.

The file is read on every request, so a test can swap it between steps.
"""
from pathlib import Path

from app.ai.errors import AINotConfigured
from app.ai.providers.base import Completion, Message, ProviderError

MAX_FILE_BYTES = 64 * 1024


class FakeAdapter:
    def __init__(self, plan_file: str, *, environment: str):
        if environment == "production":
            raise AINotConfigured("the fake provider is for development and tests only")
        if not plan_file:
            raise AINotConfigured("LLM_FAKE_PLAN_FILE is empty")
        self.path = Path(plan_file)

    def complete(self, messages: list[Message], *, model: str, json_schema, max_tokens: int,
                 timeout: float) -> Completion:
        try:
            raw = self.path.read_bytes()[:MAX_FILE_BYTES]
        except OSError:
            # bad_response, not bad_request: no long rest, the file may show up on the next try
            raise ProviderError("bad_response", detail="fake plan file not readable") from None
        return Completion(raw.decode("utf-8", errors="replace"), 0, 0)
