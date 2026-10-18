"""A local coding CLI (`claude --print`, `codex exec`) as the model - for development on a laptop
where the CLI is logged in. Never in production: it runs on someone's personal subscription."""
import shlex
import subprocess
from typing import Callable, Optional

from app.ai.errors import AINotConfigured
from app.ai.providers.base import Completion, Message, ProviderError


def render_prompt(messages: list[Message]) -> str:
    parts = []
    for m in messages:
        parts.append(f"### {m.role.upper()}\n{m.content}")
    return "\n\n".join(parts) + "\n\n### ASSISTANT\n"


class CLIAdapter:
    def __init__(self, command: str, *, environment: str, runner: Optional[Callable] = None):
        if environment == "production":
            raise AINotConfigured("the CLI provider is for local development only")
        self.argv = shlex.split(command)
        if not self.argv:
            raise AINotConfigured("LLM_CLI_COMMAND is empty")
        self.runner = runner or subprocess.run

    def complete(self, messages: list[Message], *, model: str, json_schema: Optional[dict],
                 max_tokens: int, timeout: float) -> Completion:
        argv = list(self.argv)
        if model:
            argv += ["--model", model]
        prompt = render_prompt(messages)
        try:
            done = self.runner(argv, input=prompt, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise ProviderError("timeout", detail="cli timed out") from None
        except FileNotFoundError:
            raise ProviderError("bad_request", detail="cli not found") from None
        if done.returncode != 0:
            raise ProviderError("server", detail=f"cli exit {done.returncode}")
        text = done.stdout or ""
        if not text.strip():
            raise ProviderError("bad_response", detail="cli printed nothing")
        # the CLI doesn't report tokens; ~4 chars per token is close enough for the usage table
        return Completion(text, len(prompt) // 4, len(text) // 4)
