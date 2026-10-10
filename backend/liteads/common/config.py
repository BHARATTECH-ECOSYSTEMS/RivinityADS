"""
Configuration management using Pydantic Settings.

Supports loading from environment variables and YAML files.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import Field, field_validator
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

    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    reload: bool = False
    frontend_url: str = "http://localhost:5173"


class AdServingSettings(BaseSettings):
    """Ad serving configuration."""

    default_num_ads: int = 1
    max_num_ads: int = 10
    timeout_ms: int = 50
    enable_ml_prediction: bool = True
    model_path: str = ""  # Path to CTR model for prediction


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
    """Laya sidecar configuration."""

    base_url: str = "http://laya:8000"
    timeout_ms: int = 30000


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
    env: Literal["dev", "prod", "test"] = "dev"

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
    tracking: TrackingSettings = Field(default_factory=TrackingSettings)
    geo: GeoSettings = Field(default_factory=GeoSettings)
    laya: LayaSettings = Field(default_factory=LayaSettings)
    payment: PaymentSettings = Field(default_factory=PaymentSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    groq: GroqSettings = Field(default_factory=GroqSettings)
    llm_provider: Literal["openai", "gemini", "groq"] = "openai"

    @field_validator("env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        if v not in ("dev", "prod", "test"):
            raise ValueError(f"Invalid environment: {v}")
        return v


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
    if "tracking" in merged:
        flat_config["tracking"] = TrackingSettings(**merged["tracking"])
    if "laya" in merged:
        flat_config["laya"] = LayaSettings(**merged["laya"])
    if "payment" in merged:
        flat_config["payment"] = PaymentSettings(**merged["payment"])
    if "openai" in merged:
        flat_config["openai"] = OpenAISettings(**merged["openai"])
    if "gemini" in merged:
        flat_config["gemini"] = GeminiSettings(**merged["gemini"])
    if "groq" in merged:
        flat_config["groq"] = GroqSettings(**merged["groq"])

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
    flat_config["laya"] = laya.model_copy(
        update={
            "base_url": os.getenv("LAYA_BASE_URL", laya.base_url),
        }
    )

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

    return Settings(**flat_config)


# Convenience alias
settings = get_settings()
