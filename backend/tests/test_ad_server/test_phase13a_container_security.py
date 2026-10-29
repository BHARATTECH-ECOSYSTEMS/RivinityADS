"""
Phase 13A: Staging Container Security Hardening Tests.

Validates:
1. Docker Compose database credentials do not contain hardcoded insecure defaults.
2. PostgreSQL and Redis port bindings are strictly bound to 127.0.0.1 (loopback) and not exposed on 0.0.0.0.
3. Redis authentication and healthcheck configuration is consistent with application settings.
4. Internal container networking and service hostnames are preserved.
5. Staging/production configuration fails closed on empty or known default credentials.
6. Local development workflow compatibility is safely preserved.
"""

from pathlib import Path
import pytest
import yaml

from liteads.common.config import (
    AuthSettings,
    DatabaseSettings,
    RedisSettings,
    Settings,
    TrackingSettings,
)

REPO_ROOT = Path(__file__).parent.parent.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
COMPOSE_FILE = BACKEND_DIR / "docker-compose.yml"
LOCAL_COMPOSE_FILE = BACKEND_DIR / "docker-compose.local.yml"


def _load_compose_yaml(path: Path) -> dict:
    assert path.exists(), f"Compose file not found at {path}"
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"Failed to parse compose YAML from {path}"
    return data


def test_compose_no_insecure_hardcoded_database_password():
    """Verify backend/docker-compose.yml does not contain hardcoded insecure database passwords."""
    raw_text = COMPOSE_FILE.read_text(encoding="utf-8")
    
    # Must not contain hardcoded liteads_password in the base compose file
    assert "POSTGRES_PASSWORD: liteads_password" not in raw_text, (
        "Found hardcoded 'POSTGRES_PASSWORD: liteads_password' in docker-compose.yml"
    )
    assert "LITEADS_DATABASE__PASSWORD=liteads_password" not in raw_text, (
        "Found hardcoded 'LITEADS_DATABASE__PASSWORD=liteads_password' in docker-compose.yml"
    )

    data = _load_compose_yaml(COMPOSE_FILE)
    services = data.get("services", {})

    # Check postgres service requires password
    pg_env = services["postgres"]["environment"]
    pg_pass = pg_env.get("POSTGRES_PASSWORD", "")
    assert "LITEADS_DATABASE__PASSWORD" in str(pg_pass)
    assert ":?" in str(pg_pass), "POSTGRES_PASSWORD must enforce mandatory non-empty check (:?)"

    # Check all backend services require database password
    for sname in ("ad-server", "webhook-worker", "optimization-worker", "notification-worker"):
        svc = services[sname]
        env_list = svc.get("environment", [])
        db_pass_entries = [e for e in env_list if "LITEADS_DATABASE__PASSWORD" in e]
        assert len(db_pass_entries) == 1, f"Missing LITEADS_DATABASE__PASSWORD in {sname}"
        assert ":?" in db_pass_entries[0], f"{sname} must enforce mandatory non-empty check (:?)"


def test_compose_postgres_and_redis_port_isolation():
    """Verify PostgreSQL and Redis are not exposed on all interfaces (0.0.0.0)."""
    raw_text = COMPOSE_FILE.read_text(encoding="utf-8")
    
    # Must not contain bare port publishing (which defaults to 0.0.0.0)
    assert '"5432:5432"' not in raw_text, "Found unisolated port mapping '5432:5432' in docker-compose.yml"
    assert '"6379:6379"' not in raw_text, "Found unisolated port mapping '6379:6379' in docker-compose.yml"

    data = _load_compose_yaml(COMPOSE_FILE)
    services = data.get("services", {})

    # Check PostgreSQL port binding
    pg_ports = services["postgres"].get("ports", [])
    assert len(pg_ports) == 1
    assert "127.0.0.1:5432" in str(pg_ports[0]), "PostgreSQL port must default to 127.0.0.1"

    # Check Redis port binding
    redis_ports = services["redis"].get("ports", [])
    assert len(redis_ports) == 1
    assert "127.0.0.1:6379" in str(redis_ports[0]), "Redis port must default to 127.0.0.1"


def test_compose_redis_auth_configuration_consistent():
    """Verify Redis service and app containers consume LITEADS_REDIS__PASSWORD consistently."""
    data = _load_compose_yaml(COMPOSE_FILE)
    services = data.get("services", {})
    redis_svc = services["redis"]

    # Redis command must conditionally apply --requirepass
    cmd = str(redis_svc.get("command", ""))
    assert "--requirepass" in cmd
    assert "LITEADS_REDIS__PASSWORD" in cmd

    # Redis healthcheck must conditionally supply auth
    healthcheck = redis_svc.get("healthcheck", {})
    test_cmd = str(healthcheck.get("test", []))
    assert "LITEADS_REDIS__PASSWORD" in test_cmd
    assert "-a" in test_cmd

    # App containers must pass LITEADS_REDIS__PASSWORD
    for sname in ("ad-server", "webhook-worker", "optimization-worker", "notification-worker"):
        env_list = services[sname].get("environment", [])
        assert any("LITEADS_REDIS__PASSWORD" in e for e in env_list), (
            f"LITEADS_REDIS__PASSWORD missing from {sname} environment"
        )


def test_compose_internal_service_networking():
    """Verify internal container connectivity and network definitions."""
    data = _load_compose_yaml(COMPOSE_FILE)
    services = data.get("services", {})
    networks = data.get("networks", {})

    assert "liteads-network" in networks
    
    # Verify core services attach to internal network
    for sname in ("ad-server", "postgres", "redis", "webhook-worker"):
        svc_networks = services[sname].get("networks", [])
        assert "liteads-network" in svc_networks, f"{sname} must attach to liteads-network"

    # Verify default hostnames in ad-server
    ad_env = services["ad-server"]["environment"]
    assert any("LITEADS_DATABASE__HOST" in e and "postgres" in e for e in ad_env)
    assert any("LITEADS_REDIS__HOST" in e and "redis" in e for e in ad_env)


def test_staging_configuration_rejects_empty_database_password():
    """Verify Settings rejects empty database password when env is staging."""
    with pytest.raises(ValueError, match="LITEADS_DATABASE__PASSWORD must be set and non-empty"):
        Settings(
            env="staging",
            database=DatabaseSettings(password=""),
            auth=AuthSettings(jwt_secret="a" * 32),
            tracking=TrackingSettings(secret="b" * 32),
        )


def test_staging_configuration_rejects_known_insecure_database_passwords():
    """Verify Settings rejects known development defaults in staging/production."""
    insecure_passwords = ["liteads_password", "postgres", "password", "admin", "root"]
    for bad_pwd in insecure_passwords:
        with pytest.raises(ValueError, match="Insecure default database password detected"):
            Settings(
                env="staging",
                database=DatabaseSettings(password=bad_pwd),
                auth=AuthSettings(jwt_secret="a" * 32),
                tracking=TrackingSettings(secret="b" * 32),
            )


def test_staging_configuration_rejects_example_placeholder_secrets():
    """Verify Settings rejects placeholder secrets from .env.example in staging."""
    with pytest.raises(ValueError, match="LITEADS_AUTH__JWT_SECRET cannot use example placeholder"):
        Settings(
            env="staging",
            database=DatabaseSettings(password="valid_strong_password_12345"),
            auth=AuthSettings(jwt_secret="your_jwt_secret_min_32_characters_here"),
            tracking=TrackingSettings(secret="b" * 32),
        )

    with pytest.raises(ValueError, match="LITEADS_TRACKING__SECRET cannot use example placeholder"):
        Settings(
            env="staging",
            database=DatabaseSettings(password="valid_strong_password_12345"),
            auth=AuthSettings(jwt_secret="a" * 32),
            tracking=TrackingSettings(secret="your_tracking_secret_min_32_characters_here"),
        )


def test_staging_configuration_accepts_valid_strong_credentials():
    """Verify Settings initializes cleanly in staging with valid strong credentials."""
    cfg = Settings(
        env="staging",
        database=DatabaseSettings(password="valid_strong_password_12345"),
        auth=AuthSettings(jwt_secret="c" * 32),
        tracking=TrackingSettings(secret="d" * 32),
    )
    assert cfg.env == "staging"
    assert cfg.database.password == "valid_strong_password_12345"


def test_dev_configuration_preserves_local_defaults():
    """Verify development mode permits local convenience defaults for developer experience."""
    cfg = Settings(
        env="dev",
        database=DatabaseSettings(password="liteads_password"),
        auth=AuthSettings(jwt_secret="e" * 32),
        tracking=TrackingSettings(secret="f" * 32),
    )
    assert cfg.env == "dev"
    assert cfg.database.password == "liteads_password"


def test_local_compose_override_file_present_and_valid():
    """Verify separated local development Compose override file exists and is valid."""
    assert LOCAL_COMPOSE_FILE.exists()
    data = _load_compose_yaml(LOCAL_COMPOSE_FILE)
    services = data.get("services", {})
    assert "postgres" in services
    assert "ad-server" in services
    
    # Check that local compose file provides fallback liteads_password
    pg_env = services["postgres"]["environment"]
    assert "liteads_password" in str(pg_env.get("POSTGRES_PASSWORD", ""))
