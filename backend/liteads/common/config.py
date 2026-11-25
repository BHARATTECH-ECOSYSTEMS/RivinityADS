"""
Configuration management using Pydantic Settings.

Supports loading from environment variables and YAML files.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GeoSettings(BaseSettings):
    """Geo location resolution configuration."""

    mmdb_path: str = "./GeoLite2-City.mmdb"


class DatabaseSettings(BaseSettings):
    """Database configuration."""

    host: str = "localhost"
    port: int = 5432
    name: str = "liteads"
    user: str = "liteads"
    password: str = ""
    pool_size: int = 10
    max_overflow: int = 20

    @property
    def async_url(self) -> str:
        """Get async database URL."""
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

    @property
    def sync_url(self) -> str:
        """Get sync database URL."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class RedisSettings(BaseSettings):
    """Redis configuration."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""
    pool_size: int = 10

    @property
    def url(self) -> str:
        """Get Redis URL."""
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class ServerSettings(BaseSettings):
    """Server configuration."""

    model_config = SettingsConfigDict(
        env_prefix="LITEADS_SERVER__",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    reload: bool = False
    frontend_url: str = "http://localhost:5173"
    proxy_headers: bool = True
    cors_origins: list[str] | str | None = Field(
        default=None,
        description="Explicit allowed CORS origins. In production, must be HTTPS.",
    )
    trusted_proxies: list[str] | str = Field(
        default_factory=lambda: ["127.0.0.1", "::1"],
        description="List or comma-separated string of trusted proxy IP addresses or CIDR blocks",
    )

    @field_validator("cors_origins", mode="after")
    @classmethod
    def normalize_cors_origins(cls, v: Any) -> list[str] | None:
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("[") and v.endswith("]"):
                import json

                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed if str(x).strip()]
                except Exception:
                    pass
            return [x.strip() for x in v.split(",") if x.strip()]
        elif isinstance(v, (list, tuple, set)):
            return [str(x).strip() for x in v if str(x).strip()]
        return None

    @field_validator("trusted_proxies", mode="after")
    @classmethod
    def normalize_trusted_proxies(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("[") and v.endswith("]"):
                import json

                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed if str(x).strip()]
                except Exception:
                    pass
            return [x.strip() for x in v.split(",") if x.strip()]
        elif isinstance(v, (list, tuple, set)):
            return [str(x).strip() for x in v if str(x).strip()]
        return ["127.0.0.1", "::1"]


class AdServingSettings(BaseSettings):
    """Ad serving configuration."""

    default_num_ads: int = 1
    max_num_ads: int = 10
    timeout_ms: int = 50
    enable_ml_prediction: bool = False
    prediction_mode: str = "statistical"  # "statistical", "shadow", "ml"
    model_path: str = ""  # Path to CTR model for prediction
    feature_builder_path: str = ""
    model_version: str = "v1"
    prediction_timeout_ms: float = 20.0


class FrequencySettings(BaseSettings):
    """Frequency control configuration."""

    default_daily_cap: int = 3
    default_hourly_cap: int = 1
    ttl_hours: int = 24


class MLSettings(BaseSettings):
    """ML model configuration."""

    model_dir: str = "./models"
    ctr_model: str = "deepfm_v1"
    cvr_model: str = "deepfm_cvr_v1"
    embedding_dim: int = 8
    batch_size: int = 128


class LoggingSettings(BaseSettings):
    """Logging configuration."""

    level: str = "INFO"
    format: Literal["json", "console"] = "json"


class MonitoringSettings(BaseSettings):
    """Monitoring configuration."""

    enabled: bool = True
    prometheus_port: int = 9090


class TrackingSettings(BaseSettings):
    """Tracking configuration."""

    model_config = SettingsConfigDict(
        env_prefix="LITEADS_TRACKING__",
        extra="ignore",
    )

    secret: str = ""
    signature_ttl_seconds: int = 600

    @field_validator("secret")
    @classmethod
    def tracking_secret_must_be_set(cls, v: str) -> str:
        if not v or len(v) < 32:
            raise ValueError("LITEADS_TRACKING__SECRET must be set and >= 32 chars")
        return v


class LayaSettings(BaseSettings):
    """Laya decision engine sidecar configuration.

    Laya is an external decision optimization sidecar service (not bundled in Compose).
    Set LAYA_ENABLED=false or LITEADS_LAYA__ENABLED=false when Laya is not deployed.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAYA_",
        extra="ignore",
    )

    enabled: bool = True
    base_url: str = "http://laya:8000"
    timeout_ms: int = 30000


class OptimizationSettings(BaseSettings):
    """Campaign optimization configuration (Phase 5)."""

    model_config = SettingsConfigDict(
        env_prefix="LITEADS_OPTIMIZATION__",
        extra="ignore",
    )

    enabled: bool = True
    interval_seconds: int = 900
    lock_ttl_seconds: int = 300
    lock_renewal_interval_seconds: int = 60
    campaign_lock_ttl_seconds: int = 60

    # Explicit operating mode: "shadow", "suggestions_only", "limited_pilot", "autopilot"
    mode: Literal["shadow", "suggestions_only", "limited_pilot", "autopilot"] = "suggestions_only"
    kill_switch: bool = False

    cooldown_seconds: int = 3600
    rejection_cooldown_seconds: int = 86400
    max_daily_changes_per_campaign: int = 3

    # Financial & policy guardrails
    max_budget_delta_pct: float = 0.30
    pilot_max_budget_delta_amount: float = 500.0
    pilot_campaign_ids: list[int] = Field(default_factory=list)

    @field_validator("pilot_campaign_ids", mode="before")
    @classmethod
    def parse_pilot_ids(cls, v: Any) -> list[int]:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [int(x) for x in parsed if str(x).strip().isdigit()]
                except Exception:
                    pass
            return [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
        elif isinstance(v, (list, tuple, set)):
            return [int(x) for x in v if str(x).strip().isdigit()]
        return []


class AuthSettings(BaseSettings):
    """Authentication and token configuration."""

    model_config = SettingsConfigDict(
        env_prefix="LITEADS_AUTH__",
        extra="ignore",
    )

    jwt_secret: str = ""  # NO default secret. Empty = startup failure.
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    verification_token_hours: int = 24
    password_reset_token_hours: int = 1
    max_login_attempts: int = 5
    login_window_seconds: int = 60

    @field_validator("jwt_secret")
    @classmethod
    def secret_must_be_set(cls, v: str) -> str:
        if not v or len(v) < 32:
            raise ValueError("LITEADS_AUTH__JWT_SECRET must be set and >= 32 chars")
        return v


class EmailSettings(BaseSettings):
    """Email delivery configuration."""

    model_config = SettingsConfigDict(
        env_prefix="LITEADS_EMAIL__",
        extra="ignore",
    )

    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    from_address: str = "noreply@liteads.local"
    use_tls: bool = True
    timeout_seconds: int = 10
    mock_delivery: bool = False  # Set to True in test environments


class ListmonkSettings(BaseSettings):
    """Listmonk API and transactional email delivery configuration (Phase 6.1B)."""

    model_config = SettingsConfigDict(
        env_prefix="LISTMONK_",
        extra="ignore",
    )

    enabled: bool = False
    url: str = "http://localhost:9000"
    user: str = "admin"
    token: str = ""
    request_timeout: float = 10.0
    connect_timeout: float = 3.0

    # Pinned transactional template IDs mapped via environment variables
    template_verification_id: int | None = None
    template_password_reset_id: int | None = None
    template_security_notice_id: int | None = None
    template_digest_id: int | None = None
    template_generic_id: int | None = None

    # Phase 6.1C: Marketing list mappings & consent settings
    list_newsletter_id: int | None = None
    list_advertiser_education_id: int | None = None
    list_publisher_education_id: int | None = None
    marketing_policy_version: str = "2026.1"

    # Phase 6.1D: Email marketing guards and limits
    allow_publisher_access: bool = False
    max_csv_import_rows: int = 1000

    def __repr__(self) -> str:
        """Ensure API token is strictly redacted in string representations and logs."""
        return (
            f"ListmonkSettings(enabled={self.enabled}, url={self.url!r}, user={self.user!r}, "
            f"token='[REDACTED]', request_timeout={self.request_timeout}, connect_timeout={self.connect_timeout})"
        )

    def validate_if_enabled(self) -> None:
        """Fail closed when Listmonk is enabled but required configuration is missing."""
        if not self.enabled:
            return
        if not self.url or not (self.url.startswith("http://") or self.url.startswith("https://")):
            raise ValueError("LISTMONK_URL must be a valid http/https URL when LISTMONK_ENABLED is true")
        if not self.user:
            raise ValueError("LISTMONK_USER must be non-empty when LISTMONK_ENABLED is true")
        if not self.token:
            raise ValueError("LISTMONK_TOKEN must be non-empty when LISTMONK_ENABLED is true")


class PaymentSettings(BaseSettings):
    """Payment integration configuration."""

    model_config = SettingsConfigDict(
        env_prefix="RAZORPAY_",
        extra="ignore",
    )

    key_id: str = ""
    key_secret: str = ""
    webhook_secret: str = ""


class OpenAISettings(BaseSettings):
    """OpenAI API configuration."""

    model_config = SettingsConfigDict(
        env_prefix="OPENAI_",
        extra="ignore",
    )

    api_key: str = ""


class GeminiSettings(BaseSettings):
    """Google Gemini API configuration."""

    model_config = SettingsConfigDict(
        env_prefix="GEMINI_",
        extra="ignore",
    )

    api_key: str = ""
    model: str = "gemini-flash-latest"


class GroqSettings(BaseSettings):
    """Groq API configuration."""

    model_config = SettingsConfigDict(
        env_prefix="GROQ_",
        extra="ignore",
    )

    api_key: str = ""
    model: str = "openai/gpt-oss-120b"


class WebsiteHostingSettings(BaseSettings):
    """Configuration for public website builder hosting."""

    model_config = SettingsConfigDict(
        env_prefix="LITEADS_WEBSITE__",
        extra="ignore",
    )

    platform_domain: str = "rivinity.site"
    allowed_domains: list[str] = Field(
        default_factory=lambda: ["rivinity.site", "localhost", "127.0.0.1"]
    )
    preview_ttl_seconds: int = 900
    cache_ttl_seconds: int = 3600


class ChannelSettings(BaseSettings):
    """Configuration for external channel adapters (Phase 2)."""

    model_config = SettingsConfigDict(
        env_prefix="LITEADS_CHANNELS__",
        extra="ignore",
    )

    allow_simulated_adapters: bool = False  # Strictly False by default. Gate simulations behind dev/test config that fails closed in prod.
    google_ads_api_version: str = "v18"
    google_ads_token_endpoint: str = "https://oauth2.googleapis.com/token"
    google_ads_api_endpoint: str = "https://googleads.googleapis.com"


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_prefix="LITEADS_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Application
    app_name: str = "LiteAds"
    app_version: str = "0.1.0"
    debug: bool = False
    env: Literal["dev", "prod", "test", "staging"] = "dev"
    enable_in_process_schedulers: bool | None = Field(
        default=None,
        description="Master switch for in-process background worker loops. "
        "In dev/test, defaults to True (monolith mode). "
        "In prod/staging, defaults to False (dedicated worker topology).",
    )
    enable_webhook_worker: bool = Field(
        default=True,
        description="Whether to run the durable webhook outbox worker in-process within the API server. "
        "Set to false (LITEADS_ENABLE_WEBHOOK_WORKER=false) when running a dedicated worker container.",
    )
    enable_optimization_worker: bool = Field(
        default=True,
        description="Whether to run the optimization loop scheduler in-process within the API server. "
        "Set to false (LITEADS_ENABLE_OPTIMIZATION_WORKER=false) when running a dedicated worker container.",
    )
    enable_notification_worker: bool = Field(
        default=True,
        description="Whether to run the durable notification delivery worker in-process within the API server. "
        "Set to false (LITEADS_ENABLE_NOTIFICATION_WORKER=false) when running a dedicated worker container.",
    )
    enable_digest_worker: bool = Field(
        default=True,
        description="Whether to run the weekly digest delivery worker in-process within the API server. "
        "Set to false (LITEADS_ENABLE_DIGEST_WORKER=false) when running a dedicated worker container.",
    )

    # Nested settings
    server: ServerSettings = Field(default_factory=ServerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    ad_serving: AdServingSettings = Field(default_factory=AdServingSettings)
    frequency: FrequencySettings = Field(default_factory=FrequencySettings)
    ml: MLSettings = Field(default_factory=MLSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    listmonk: ListmonkSettings = Field(default_factory=ListmonkSettings)
    tracking: TrackingSettings = Field(default_factory=TrackingSettings)
    geo: GeoSettings = Field(default_factory=GeoSettings)
    laya: LayaSettings = Field(default_factory=LayaSettings)
    optimization: OptimizationSettings = Field(default_factory=OptimizationSettings)
    payment: PaymentSettings = Field(default_factory=PaymentSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    groq: GroqSettings = Field(default_factory=GroqSettings)
    website: WebsiteHostingSettings = Field(default_factory=WebsiteHostingSettings)
    channels: ChannelSettings = Field(default_factory=ChannelSettings)
    llm_provider: Literal["openai", "gemini", "groq"] = "openai"

    @field_validator("env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        if v not in ("dev", "prod", "test", "staging"):
            raise ValueError(f"Invalid environment: {v}")
        return v

    @model_validator(mode="after")
    def validate_staging_prod_credentials(self) -> "Settings":
        # Resolve in-process scheduler default based on topology
        if self.enable_in_process_schedulers is None:
            if self.env in ("staging", "prod"):
                self.enable_in_process_schedulers = False
            else:
                self.enable_in_process_schedulers = True

        if self.env in ("staging", "prod"):
            # 0. Simulated channel adapters are strictly prohibited in production and staging
            if self.channels.allow_simulated_adapters:
                raise ValueError(
                    "LITEADS_CHANNELS__ALLOW_SIMULATED_ADAPTERS cannot be True in staging/prod environment. "
                    "Simulated channel adapters are strictly prohibited in production."
                )

            # 1. Database password must not be empty or known dev default
            db_pwd = self.database.password
            if not db_pwd:
                raise ValueError(
                    "LITEADS_DATABASE__PASSWORD must be set and non-empty in staging/prod environment"
                )
            if db_pwd.lower() in ("liteads_password", "postgres", "password", "admin", "root", "secret", "changeme"):
                raise ValueError(
                    "Insecure default database password detected. "
                    "LITEADS_DATABASE__PASSWORD must not use known default values in staging/prod environment"
                )

            # 2. Redis password must be set and not default in production
            if self.env == "prod":
                redis_pwd = self.redis.password
                if not redis_pwd:
                    raise ValueError(
                        "LITEADS_REDIS__PASSWORD must be set and non-empty in production environment"
                    )
                if redis_pwd.lower() in ("redis", "password", "admin", "root", "secret", "changeme", "liteads"):
                    raise ValueError(
                        "Insecure default Redis password detected in production environment"
                    )

            # 3. JWT & Tracking secrets cannot use example placeholder values or be short/weak
            jwt = self.auth.jwt_secret
            if not jwt or len(jwt) < 32 or jwt in (
                "your_jwt_secret_min_32_characters_here",
                "your_jwt_secret_here",
                "test_jwt_secret_must_be_at_least_32_characters_long_for_validation",
                "changeme_must_be_at_least_32_characters_long",
            ):
                raise ValueError(
                    "LITEADS_AUTH__JWT_SECRET must be at least 32 characters and cannot use default/placeholder strings in staging/prod"
                )

            trk = self.tracking.secret
            if not trk or len(trk) < 32 or trk in (
                "your_tracking_secret_min_32_characters_here",
                "your_tracking_secret_here",
                "test_tracking_secret_must_be_at_least_32_characters_long_for_validation",
                "changeme_must_be_at_least_32_characters_long",
            ):
                raise ValueError(
                    "LITEADS_TRACKING__SECRET must be at least 32 characters and cannot use default/placeholder strings in staging/prod"
                )

            # 4. HTTPS origins enforcement
            if self.env == "prod":
                if self.server.frontend_url and not self.server.frontend_url.startswith("https://"):
                    raise ValueError(
                        f"Insecure frontend_url '{self.server.frontend_url}' in production. Must use HTTPS."
                    )
                if self.server.cors_origins:
                    for origin in self.server.cors_origins:
                        if origin == "*":
                            raise ValueError(
                                "Wildcard CORS origin '*' is strictly prohibited in production."
                            )
                        if not origin.startswith("https://"):
                            raise ValueError(
                                f"Insecure CORS origin '{origin}' in production. All allowed origins must use HTTPS."
                            )

            # 5. Trusted proxy configuration
            if self.env == "prod":
                if not self.server.trusted_proxies:
                    raise ValueError(
                        "LITEADS_SERVER__TRUSTED_PROXIES must be configured in production for secure client IP resolution."
                    )

            # 6. Email configuration
            if self.env == "prod" and not self.listmonk.enabled:
                if self.email.host in ("mailpit", "localhost", "127.0.0.1") and self.email.port == 1025:
                    raise ValueError(
                        "Mailpit/local plaintext SMTP is development-only and cannot be used in production."
                    )

        if self.listmonk.enabled:
            self.listmonk.validate_if_enabled()
        return self


def load_yaml_config(config_path: Path) -> dict:
    """Load configuration from YAML file."""
    if not config_path.exists():
        return {}

    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def merge_configs(base: dict, override: dict) -> dict:
    """Deep merge two configuration dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = value
    return result


@lru_cache
def get_settings() -> Settings:
    """
    Get application settings.

    Loads from:
    1. configs/base.yaml (base configuration)
    2. configs/{env}.yaml (environment-specific overrides)
    3. Environment variables (highest priority)
    """
    import os

    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    env = os.getenv("LITEADS_ENV", "dev")

    # Find config directory
    config_dir = Path(__file__).parent.parent.parent / "configs"

    # Load base config
    base_config = load_yaml_config(config_dir / "base.yaml")

    # Load environment-specific config
    env_config = load_yaml_config(config_dir / f"{env}.yaml")
    if not env_config and env == "staging":
        env_config = load_yaml_config(config_dir / "prod.yaml")

    # Merge configurations
    merged = merge_configs(base_config, env_config)

    # Flatten nested config for Pydantic
    flat_config = {}
    if "app" in merged:
        flat_config["app_name"] = merged["app"].get("name", "LiteAds")
        flat_config["app_version"] = merged["app"].get("version", "0.1.0")
        flat_config["debug"] = merged["app"].get("debug", False)

    flat_config["env"] = env

    # Create nested settings
    if "server" in merged:
        flat_config["server"] = ServerSettings(**merged["server"])
    if "database" in merged:
        flat_config["database"] = DatabaseSettings(**merged["database"])
    if "redis" in merged:
        flat_config["redis"] = RedisSettings(**merged["redis"])
    if "ad_serving" in merged:
        flat_config["ad_serving"] = AdServingSettings(**merged["ad_serving"])
    if "frequency" in merged:
        flat_config["frequency"] = FrequencySettings(**merged["frequency"])
    if "ml" in merged:
        flat_config["ml"] = MLSettings(**merged["ml"])
    if "logging" in merged:
        flat_config["logging"] = LoggingSettings(**merged["logging"])
    if "monitoring" in merged:
        flat_config["monitoring"] = MonitoringSettings(**merged["monitoring"])
    if "auth" in merged:
        flat_config["auth"] = AuthSettings(**merged["auth"])
    if "email" in merged:
        flat_config["email"] = EmailSettings(**merged["email"])
    if "listmonk" in merged:
        flat_config["listmonk"] = ListmonkSettings(**merged["listmonk"])
    if "tracking" in merged:
        flat_config["tracking"] = TrackingSettings(**merged["tracking"])
    if "laya" in merged:
        flat_config["laya"] = LayaSettings(**merged["laya"])
    if "optimization" in merged:
        flat_config["optimization"] = OptimizationSettings(**merged["optimization"])
    if "payment" in merged:
        flat_config["payment"] = PaymentSettings(**merged["payment"])
    if "openai" in merged:
        flat_config["openai"] = OpenAISettings(**merged["openai"])
    if "gemini" in merged:
        flat_config["gemini"] = GeminiSettings(**merged["gemini"])
    if "groq" in merged:
        flat_config["groq"] = GroqSettings(**merged["groq"])
    if "website" in merged:
        flat_config["website"] = WebsiteHostingSettings(**merged["website"])

    # Nested settings are created from YAML above, so apply environment overrides
    # explicitly for deployments such as Docker Compose.
    database = flat_config.get("database", DatabaseSettings())
    flat_config["database"] = database.model_copy(
        update={
            "host": os.getenv("LITEADS_DATABASE__HOST", database.host),
            "port": int(os.getenv("LITEADS_DATABASE__PORT", database.port)),
            "name": os.getenv("LITEADS_DATABASE__NAME", database.name),
            "user": os.getenv("LITEADS_DATABASE__USER", database.user),
            "password": os.getenv("LITEADS_DATABASE__PASSWORD", database.password),
        }
    )

    server = flat_config.get("server", ServerSettings())
    flat_config["server"] = server.model_copy(
        update={
            "host": os.getenv("LITEADS_SERVER__HOST", server.host),
            "port": int(os.getenv("LITEADS_SERVER__PORT", server.port)),
            "frontend_url": os.getenv("LITEADS_SERVER__FRONTEND_URL", server.frontend_url),
        }
    )

    redis = flat_config.get("redis", RedisSettings())
    flat_config["redis"] = redis.model_copy(
        update={
            "host": os.getenv("LITEADS_REDIS__HOST", redis.host),
            "port": int(os.getenv("LITEADS_REDIS__PORT", redis.port)),
            "db": int(os.getenv("LITEADS_REDIS__DB", redis.db)),
            "password": os.getenv("LITEADS_REDIS__PASSWORD", redis.password),
        }
    )

    email = flat_config.get("email", EmailSettings())
    flat_config["email"] = email.model_copy(
        update={
            "host": os.getenv("LITEADS_EMAIL__HOST", email.host),
            "port": int(os.getenv("LITEADS_EMAIL__PORT", str(email.port))),
            "user": os.getenv("LITEADS_EMAIL__USER", email.user),
            "password": os.getenv("LITEADS_EMAIL__PASSWORD", email.password),
            "from_address": os.getenv("LITEADS_EMAIL__FROM_ADDRESS", email.from_address),
            "mock_delivery": os.getenv("LITEADS_EMAIL__MOCK_DELIVERY", str(email.mock_delivery)).lower() in ("true", "1", "yes"),
        }
    )

    listmonk = flat_config.get("listmonk", ListmonkSettings())
    listmonk_updates = {}
    lm_enabled = os.getenv("LISTMONK_ENABLED")
    if lm_enabled is not None:
        listmonk_updates["enabled"] = lm_enabled.lower() in ("true", "1", "yes", "on")
    if os.getenv("LISTMONK_URL"):
        listmonk_updates["url"] = os.getenv("LISTMONK_URL")
    if os.getenv("LISTMONK_USER"):
        listmonk_updates["user"] = os.getenv("LISTMONK_USER")
    if os.getenv("LISTMONK_TOKEN"):
        listmonk_updates["token"] = os.getenv("LISTMONK_TOKEN")
    if os.getenv("LISTMONK_REQUEST_TIMEOUT"):
        try:
            listmonk_updates["request_timeout"] = float(os.getenv("LISTMONK_REQUEST_TIMEOUT"))
        except ValueError:
            pass
    if os.getenv("LISTMONK_CONNECT_TIMEOUT"):
        try:
            listmonk_updates["connect_timeout"] = float(os.getenv("LISTMONK_CONNECT_TIMEOUT"))
        except ValueError:
            pass
    for tmpl_field, env_var in [
        ("template_verification_id", "LISTMONK_TEMPLATE_VERIFICATION_ID"),
        ("template_password_reset_id", "LISTMONK_TEMPLATE_PASSWORD_RESET_ID"),
        ("template_security_notice_id", "LISTMONK_TEMPLATE_SECURITY_NOTICE_ID"),
        ("template_digest_id", "LISTMONK_TEMPLATE_DIGEST_ID"),
        ("template_generic_id", "LISTMONK_TEMPLATE_GENERIC_ID"),
        ("list_newsletter_id", "LISTMONK_LIST_NEWSLETTER_ID"),
        ("list_advertiser_education_id", "LISTMONK_LIST_ADVERTISER_EDUCATION_ID"),
        ("list_publisher_education_id", "LISTMONK_LIST_PUBLISHER_EDUCATION_ID"),
    ]:
        val = os.getenv(env_var)
        if val is not None and val.strip().isdigit():
            listmonk_updates[tmpl_field] = int(val.strip())

    if os.getenv("MARKETING_POLICY_VERSION"):
        listmonk_updates["marketing_policy_version"] = os.getenv("MARKETING_POLICY_VERSION")
    if os.getenv("LISTMONK_ALLOW_PUBLISHER_ACCESS") is not None:
        listmonk_updates["allow_publisher_access"] = os.getenv("LISTMONK_ALLOW_PUBLISHER_ACCESS", "").lower() in ("true", "1", "yes", "on")
    if os.getenv("LISTMONK_MAX_CSV_IMPORT_ROWS") and os.getenv("LISTMONK_MAX_CSV_IMPORT_ROWS").strip().isdigit():
        listmonk_updates["max_csv_import_rows"] = int(os.getenv("LISTMONK_MAX_CSV_IMPORT_ROWS").strip())

    flat_config["listmonk"] = listmonk.model_copy(update=listmonk_updates)

    openai = flat_config.get("openai", OpenAISettings())
    flat_config["openai"] = openai.model_copy(
        update={"api_key": os.getenv("OPENAI_API_KEY", openai.api_key)}
    )
    gemini = flat_config.get("gemini", GeminiSettings())
    flat_config["gemini"] = gemini.model_copy(
        update={
            "api_key": os.getenv("GEMINI_API_KEY", gemini.api_key),
            "model": os.getenv("GEMINI_MODEL", gemini.model),
        }
    )
    groq = flat_config.get("groq", GroqSettings())
    flat_config["groq"] = groq.model_copy(
        update={
            "api_key": os.getenv("GROQ_API_KEY", groq.api_key),
            "model": os.getenv("GROQ_MODEL", groq.model),
        }
    )
    flat_config["llm_provider"] = os.getenv("LLM_PROVIDER", "openai").lower()

    laya = flat_config.get("laya", LayaSettings())
    laya_enabled_env = (
        os.getenv("LITEADS_LAYA__ENABLED")
        or os.getenv("LITEADS_LAYA_ENABLED")
        or os.getenv("LAYA_ENABLED")
    )
    laya_updates = {
        "base_url": os.getenv("LITEADS_LAYA__BASE_URL") or os.getenv("LAYA_BASE_URL", laya.base_url),
    }
    if laya_enabled_env is not None:
        laya_updates["enabled"] = laya_enabled_env.lower() in ("true", "1", "yes", "on")

    flat_config["laya"] = laya.model_copy(update=laya_updates)

    payment = flat_config.get("payment", PaymentSettings())
    flat_config["payment"] = payment.model_copy(
        update={
            "key_id": os.getenv("RAZORPAY_KEY_ID", payment.key_id),
            "key_secret": os.getenv("RAZORPAY_KEY_SECRET", payment.key_secret),
            "webhook_secret": os.getenv("RAZORPAY_WEBHOOK_SECRET", payment.webhook_secret),
        }
    )

    openai = flat_config.get("openai", OpenAISettings())
    flat_config["openai"] = openai.model_copy(
        update={
            "api_key": os.getenv("OPENAI_API_KEY", openai.api_key),
        }
    )

    enable_worker_env = os.getenv("LITEADS_ENABLE_WEBHOOK_WORKER")
    if enable_worker_env is not None:
        flat_config["enable_webhook_worker"] = enable_worker_env.lower() in ("true", "1", "yes", "on")
    else:
        flat_config["enable_webhook_worker"] = True

    enable_notif_worker_env = os.getenv("LITEADS_ENABLE_NOTIFICATION_WORKER")
    if enable_notif_worker_env is not None:
        flat_config["enable_notification_worker"] = enable_notif_worker_env.lower() in ("true", "1", "yes", "on")
    else:
        flat_config["enable_notification_worker"] = True

    optimization = flat_config.get("optimization", OptimizationSettings())
    opt_updates = {}
    
    # 1. Mode
    opt_mode = (
        os.getenv("LITEADS_OPTIMIZATION__MODE")
        or os.getenv("LITEADS_OPTIMIZATION_MODE")
        or os.getenv("OPTIMIZATION_MODE")
    )
    if opt_mode is not None and opt_mode.strip() != "":
        opt_mode_clean = opt_mode.strip().lower()
        if opt_mode_clean in ("shadow", "suggestions_only", "limited_pilot", "autopilot"):
            opt_updates["mode"] = opt_mode_clean
        else:
            raise ValueError(
                f"Invalid optimization mode '{opt_mode}'. Supported modes are: "
                "'shadow', 'suggestions_only', 'limited_pilot', 'autopilot'."
            )

    # 2. Kill Switch
    opt_kill = (
        os.getenv("LITEADS_OPTIMIZATION__KILL_SWITCH")
        or os.getenv("LITEADS_OPTIMIZATION_KILL_SWITCH")
        or os.getenv("OPTIMIZATION_KILL_SWITCH")
    )
    if opt_kill is not None:
        opt_updates["kill_switch"] = opt_kill.lower() in ("true", "1", "yes", "on")

    # 3. Interval Seconds
    opt_interval = (
        os.getenv("LITEADS_OPTIMIZATION__INTERVAL_SECONDS")
        or os.getenv("LITEADS_OPTIMIZATION_INTERVAL_SECONDS")
        or os.getenv("OPTIMIZATION_INTERVAL_SECONDS")
    )
    if opt_interval and opt_interval.isdigit():
        opt_updates["interval_seconds"] = int(opt_interval)

    # 4. Cooldown Seconds
    opt_cooldown = (
        os.getenv("LITEADS_OPTIMIZATION__COOLDOWN_SECONDS")
        or os.getenv("OPTIMIZATION_COOLDOWN_SECONDS")
    )
    if opt_cooldown and opt_cooldown.isdigit():
        opt_updates["cooldown_seconds"] = int(opt_cooldown)

    # 5. Rejection Cooldown Seconds
    opt_rej_cooldown = (
        os.getenv("LITEADS_OPTIMIZATION__REJECTION_COOLDOWN_SECONDS")
        or os.getenv("OPTIMIZATION_REJECTION_COOLDOWN_SECONDS")
    )
    if opt_rej_cooldown and opt_rej_cooldown.isdigit():
        opt_updates["rejection_cooldown_seconds"] = int(opt_rej_cooldown)

    # 6. Max Daily Changes
    opt_daily_changes = (
        os.getenv("LITEADS_OPTIMIZATION__MAX_DAILY_CHANGES_PER_CAMPAIGN")
        or os.getenv("LITEADS_OPTIMIZATION__MAX_DAILY_CHANGES")
        or os.getenv("OPTIMIZATION_MAX_DAILY_CHANGES_PER_CAMPAIGN")
        or os.getenv("OPTIMIZATION_MAX_DAILY_CHANGES")
    )
    if opt_daily_changes and opt_daily_changes.isdigit():
        opt_updates["max_daily_changes_per_campaign"] = int(opt_daily_changes)

    # 7. Pilot Max Budget Delta
    opt_pilot_delta = (
        os.getenv("LITEADS_OPTIMIZATION__PILOT_MAX_BUDGET_DELTA_AMOUNT")
        or os.getenv("OPTIMIZATION_PILOT_MAX_BUDGET_DELTA_AMOUNT")
    )
    if opt_pilot_delta:
        try:
            opt_updates["pilot_max_budget_delta_amount"] = float(opt_pilot_delta)
        except ValueError:
            pass

    flat_config["optimization"] = optimization.model_copy(update=opt_updates)

    enable_opt_worker_env = (
        os.getenv("LITEADS_ENABLE_OPTIMIZATION_WORKER")
        or os.getenv("ENABLE_OPTIMIZATION_WORKER")
    )
    if enable_opt_worker_env is not None:
        flat_config["enable_optimization_worker"] = enable_opt_worker_env.lower() in ("true", "1", "yes", "on")
    else:
        flat_config["enable_optimization_worker"] = True

    return Settings(**flat_config)


# Convenience alias
settings = get_settings()
