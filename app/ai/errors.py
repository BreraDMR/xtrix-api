"""Errors the assistant layer raises. Endpoints map them to HTTP answers:
AILimitReached -> 429 with Retry-After (the user's own allowance is used up),
AIBusy -> 503 with Retry-After (our provider slots are resting - not the user's fault),
AINotConfigured / AIUnavailable -> 503."""


class AIError(Exception):
    code = "ai_error"

    def __init__(self, message: str = "", *, prompt_tokens: int = 0, completion_tokens: int = 0):
        super().__init__(message or self.code)
        # tokens already burnt before giving up, so usage accounting can still bill them
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class AINotConfigured(AIError):
    """No usable model: no slot has a key, or the chosen provider can't run here."""
    code = "ai_not_configured"


class AIUnavailable(AIError):
    """Every slot was tried and none gave a valid answer, and none is just cooling down."""
    code = "ai_unavailable"


class AIBusy(AIError):
    """Every slot is cooling down after rate limits. retry_after is in seconds."""
    code = "ai_busy"

    def __init__(self, retry_after: int, **kwargs):
        super().__init__(f"all model slots are cooling down, retry in {retry_after}s", **kwargs)
        self.retry_after = retry_after


class AILimitReached(AIError):
    """The business used up its assistant requests - monthly normally, daily in the pilot."""
    code = "ai_limit_reached"

    def __init__(self, limit: int, used: int, retry_after: int, period: str = "month"):
        super().__init__(f"{period}ly assistant limit reached ({used}/{limit})")
        self.limit = limit
        self.used = used
        self.retry_after = retry_after
        self.period = period
