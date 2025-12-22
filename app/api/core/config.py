from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = Field(
        ...,  # Обязательное поле - должно быть в .env
        env="DATABASE_URL",
        description="SQLAlchemy DSN (postgresql://user:password@host:port/dbname)",
    )
    redis_url: str = Field("redis://localhost:6379/0", env="REDIS_URL")
    jwt_secret: str = Field(
        ...,  # Обязательное поле - должно быть в .env, минимум 32 символа
        env="JWT_SECRET",
        min_length=32,
        description="JWT secret key for token signing (минимум 32 символа)",
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    refresh_token_expire_days: int = 30
    
    # Account lockout settings
    max_login_attempts: int = Field(
        default=5,
        env="MAX_LOGIN_ATTEMPTS",
        description="Maximum failed login attempts before account lockout",
    )
    lockout_duration_minutes: int = Field(
        default=30,
        env="LOCKOUT_DURATION_MINUTES",
        description="Account lockout duration in minutes",
    )
    aws_access_key: str = Field("", env="AWS_ACCESS_KEY_ID")
    aws_secret_key: str = Field("", env="AWS_SECRET_ACCESS_KEY")
    aws_region: str = Field("us-east-1", env="AWS_REGION")
    aws_bucket: str = Field("", env="AWS_BUCKET")
    stripe_api_key: str = Field("", env="STRIPE_API_KEY")
    stripe_webhook_secret: str = Field("", env="STRIPE_WEBHOOK_SECRET")
    apple_shared_secret: str = Field("", env="APPLE_SHARED_SECRET")
    apple_webhook_token: str = Field("", env="APPLE_WEBHOOK_TOKEN")
    google_service_account_json: str = Field("", env="GOOGLE_SERVICE_ACCOUNT_JSON")
    google_webhook_token: str = Field("", env="GOOGLE_WEBHOOK_TOKEN")
    smtp_host: str = Field("", env="SMTP_HOST")
    smtp_port: int = Field(587, env="SMTP_PORT")
    smtp_user: str = Field("", env="SMTP_USER")
    smtp_password: str = Field("", env="SMTP_PASSWORD")
    smtp_from: str = Field("", env="SMTP_FROM")
    twilio_sid: str = Field("", env="TWILIO_SID")
    twilio_token: str = Field("", env="TWILIO_TOKEN")
    twilio_from: str = Field("", env="TWILIO_FROM")
    
    # CORS settings
    cors_origins: str = Field(
        default="",
        env="CORS_ORIGINS",
        description="Allowed CORS origins (comma-separated)",
    )
    
    def get_cors_origins_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        if not self.cors_origins:
            return []
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
    environment: str = Field(
        default="development",
        env="ENVIRONMENT",
        description="Environment: development, staging, production",
    )
    
    # Rate Limiting settings
    rate_limit_enabled: bool = Field(
        default=False,  # По умолчанию отключено для разработки
        env="RATE_LIMIT_ENABLED",
        description="Enable rate limiting",
    )
    rate_limit_default: str = Field(
        default="1000/hour",
        env="RATE_LIMIT_DEFAULT",
        description="Default rate limit (e.g., '1000/hour', '100/minute')",
    )
    rate_limit_login: str = Field(
        default="5/minute",
        env="RATE_LIMIT_LOGIN",
        description="Rate limit for login endpoint",
    )
    
    # Logging settings
    log_level: str = Field(
        default="INFO",
        env="LOG_LEVEL",
        description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL",
    )
    log_file: str = Field(
        default="logs/app.log",
        env="LOG_FILE",
        description="Path to log file",
    )
    log_errors_file: str = Field(
        default="logs/errors.log",
        env="LOG_ERRORS_FILE",
        description="Path to errors log file",
    )
    
    # Security Headers settings
    csp_policy: str = Field(
        default="",
        env="CSP_POLICY",
        description="Content-Security-Policy header value (empty for default)",
    )
    enable_hsts: bool = Field(
        default=True,
        env="ENABLE_HSTS",
        description="Enable HSTS header (only for HTTPS)",
    )
    
    # Development settings
    detailed_errors: bool = Field(
        default=True,
        env="DETAILED_ERRORS",
        description="Enable detailed error messages (recommended for development only)",
    )
    
    # Frontend URL for QR code links
    frontend_url: str = Field(
        default="http://localhost:3000",
        env="FRONTEND_URL",
        description="Frontend URL for generating QR code access links",
    )

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
