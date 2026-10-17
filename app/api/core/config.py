from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
PLACEHOLDER_JWT_SECRETS = {
    "change-me-minimum-32-characters-long",
    "test-secret-key-minimum-32-characters-long-for-testing",
    "put-a-random-string-of-at-least-32-chars-here",
}
class Settings(BaseSettings):
    database_url: str = Field(
        ...,
        description="SQLAlchemy DSN (postgresql://user:password@host:port/dbname)",
    )
    @field_validator("database_url")
    @classmethod
    def use_psycopg2_driver(cls, v: str) -> str:
        # SQLAlchemy 2.1 maps a bare postgresql:// to psycopg 3, but we ship psycopg2.
        # Railway & co hand out bare URLs, so pin the driver here once for everyone.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg2://" + v[len(prefix):]
        return v
    redis_url: str = Field("redis://localhost:6379/0")
    jwt_secret: str = Field(
        ...,
        min_length=32,
        description="JWT secret key for token signing (at least 32 chars)",
    )
    jwt_algorithm: str = "HS256"
    # short on purpose: a leaked access token is good for half an hour, the client refreshes
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    max_login_attempts: int = Field(
        default=5,
        description="Failed logins per (account, IP) before that pair is refused",
    )
    lockout_duration_minutes: int = Field(
        default=30,
        description="Window for max_login_attempts per (account, IP) - that pair is refused for it",
    )
    login_account_failure_limit: str = Field(
        default="50/hour",
        description="Failed logins per account from all IPs; past it only IPs with no failures may try",
    )
    # the env names differ from the field names here; the old env="..." kwarg was silently ignored
    aws_access_key: str = Field("", validation_alias=AliasChoices("AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY"))
    aws_secret_key: str = Field("", validation_alias=AliasChoices("AWS_SECRET_ACCESS_KEY", "AWS_SECRET_KEY"))
    aws_region: str = Field("us-east-1")
    aws_bucket: str = Field("")
    stripe_api_key: str = Field("")
    stripe_webhook_secret: str = Field("")
    apple_shared_secret: str = Field("")
    apple_webhook_token: str = Field("")
    google_service_account_json: str = Field("")
    google_webhook_token: str = Field("")
    erl_webhook_token: str = Field("")
    smtp_host: str = Field("")
    smtp_port: int = Field(587)
    smtp_user: str = Field("")
    smtp_password: str = Field("")
    smtp_from: str = Field("")
    twilio_sid: str = Field("")
    twilio_token: str = Field("")
    twilio_from: str = Field("")
    twilio_whatsapp_from: str = Field("", description="Twilio WhatsApp sender (format: whatsapp:+1234567890)")
    sendpulse_client_id: str = Field("", description="SendPulse API Client ID")
    sendpulse_client_secret: str = Field("", description="SendPulse API Client Secret")
    sendpulse_address_book_id: int = Field(0, description="SendPulse Address Book ID for contacts")
    sendpulse_from_email: str = Field("", description="SendPulse sender email address")
    sendpulse_from_name: str = Field("", description="SendPulse sender name")
    sendpulse_sms_sender: str = Field("", description="SendPulse SMS sender name")
    cors_origins: str = Field(
        default="",
        description="Allowed CORS origins (comma-separated)",
    )
    def get_cors_origins_list(self) -> list[str]:
        if not self.cors_origins:
            return []
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
    environment: str = Field(
        default="development",
        description="Environment: development, staging, production",
    )
    rate_limit_enabled: bool = Field(
        default=True,
        description="Enable rate limiting",
    )
    rate_limit_default: str = Field(
        default="1000/hour",
        description="Default rate limit (e.g., '1000/hour', '100/minute')",
    )
    rate_limit_login: str = Field(
        default="10/minute",
        description="Per-IP rate limit for login (a salon's staff often share one IP; per-account protection is separate)",
    )
    rate_limit_signup: str = Field(
        default="5/minute;30/hour",
        description="Per-IP rate limit for public signup",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL",
    )
    log_file: str = Field(
        default="logs/app.log",
        description="Path to log file",
    )
    log_errors_file: str = Field(
        default="logs/errors.log",
        description="Path to errors log file",
    )
    trust_proxy: bool = Field(False, description="Behind our own proxy (Render etc.): take the client IP from the last X-Forwarded-For hop")
    docs_username: str = Field("", description="HTTP Basic login for /docs in production (empty = docs off)")
    docs_password: str = Field("", description="HTTP Basic password for /docs in production")
    csp_policy: str = Field(
        default="",
        description="Content-Security-Policy header value (empty for default)",
    )
    enable_hsts: bool = Field(
        default=True,
        description="Enable HSTS header (only for HTTPS)",
    )
    detailed_errors: bool = Field(
        default=False,
        description="Enable detailed error messages (recommended for development only)",
    )
    frontend_url: str = Field(
        default="http://localhost:3000",
        description="Frontend URL for generating QR code access links",
    )
    @model_validator(mode="after")
    def no_placeholder_secret_in_production(self):
        # these strings are public (repo, compose, CI) - a token signed with them can be forged by anyone
        if self.environment == "production" and self.jwt_secret in PLACEHOLDER_JWT_SECRETS:
            raise ValueError("JWT_SECRET is a public placeholder, set a real random secret in production")
        return self
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, populate_by_name=True)
settings = Settings()
