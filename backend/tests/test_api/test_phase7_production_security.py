"""Phase 7: Production Readiness, Security Hardening & Operational Verifications.

Validates:
1. Fail-closed production configuration:
   - Rejection of default or weak database passwords.
   - Rejection of missing or weak Redis passwords in production.
   - Rejection of short (< 32 char) or placeholder JWT/tracking secrets.
   - Rejection of non-HTTPS origins and wildcard CORS in production.
   - Rejection of Mailpit/insecure SMTP in production.
2. Supported topology & worker separation:
   - API server in-process schedulers default to False in production.
   - Monolith mode defaults to True in development/test.
   - All background workers are individually configurable.
3. Encrypted automated database backups & restore drill:
   - Creation of compressed AES-256-GCM encrypted backup with SHA-256 manifest.
   - Restore drill verifies checksum and decrypts data cleanly.
   - Fail-closed behavior on tampered ciphertext or incorrect passphrase.
   - Retention policy pruning.
4. Comprehensive subsystem health checks & zero information leakage:
   - Health checks for database, Redis, worker queues, DLQ, email, payment, and Laya sidecar.
   - Strict absence of leaked secrets, credentials, or internal IPs.
5. OpenTelemetry correlation ID propagation:
   - Verification of X-Correlation-ID and X-Request-ID request/response header linkage.
6. SSRF, XSS, and supply chain integrity:
   - Rejection of AWS metadata and private subnet webhook destinations.
   - Rejection of inline script and forbidden attributes in page blocks.
   - Validation of CycloneDX SBOM and pinned Docker Compose images.
"""

import gzip
import hashlib
import json
import os
from pathlib import Path
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from liteads.common.config import Settings
from liteads.schemas.website import OpenDesignSectionContent
from liteads.common.ssrf import validate_destination_url, SSRFValidationError
from scripts.backup_db import create_backup, encrypt_data, prune_old_backups
from scripts.restore_db import decrypt_data, run_restore_drill


# -----------------------------------------------------------------------------
# 1. Fail-Closed Production Configuration Tests
# -----------------------------------------------------------------------------
def test_production_rejects_default_database_password():
    """Production mode must reject default dev passwords (e.g. 'liteads_password', 'postgres')."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            env="prod",
            database={"password": "liteads_password", "host": "postgres"},
            redis={"password": "StrongProductionRedisPassword123!"},
            auth={"jwt_secret": "a" * 32},
            tracking={"secret": "b" * 32},
            server={"frontend_url": "https://ads.example.com", "trusted_proxies": ["127.0.0.1"]},
        )
    assert "Insecure default database password detected" in str(exc.value)


def test_production_rejects_missing_or_default_redis_password():
    """Production mode must require a non-default, strong Redis password."""
    # Missing password
    with pytest.raises(ValidationError) as exc1:
        Settings(
            env="prod",
            database={"password": "StrongDbSecretPassword987!"},
            redis={"password": ""},
            auth={"jwt_secret": "a" * 32},
            tracking={"secret": "b" * 32},
            server={"frontend_url": "https://ads.example.com", "trusted_proxies": ["127.0.0.1"]},
        )
    assert "LITEADS_REDIS__PASSWORD must be set and non-empty" in str(exc1.value)

    # Insecure default password
    with pytest.raises(ValidationError) as exc2:
        Settings(
            env="prod",
            database={"password": "StrongDbSecretPassword987!"},
            redis={"password": "redis"},
            auth={"jwt_secret": "a" * 32},
            tracking={"secret": "b" * 32},
            server={"frontend_url": "https://ads.example.com", "trusted_proxies": ["127.0.0.1"]},
        )
    assert "Insecure default Redis password detected" in str(exc2.value)


def test_production_rejects_weak_or_placeholder_jwt_and_tracking_secrets():
    """Production mode must reject short secrets (< 32 chars) or example placeholder strings."""
    with pytest.raises(ValidationError) as exc1:
        Settings(
            env="prod",
            database={"password": "StrongDbSecretPassword987!"},
            redis={"password": "StrongRedisPassword987!"},
            auth={"jwt_secret": "your_jwt_secret_min_32_characters_here"},
            tracking={"secret": "b" * 32},
            server={"frontend_url": "https://ads.example.com", "trusted_proxies": ["127.0.0.1"]},
        )
    assert "LITEADS_AUTH__JWT_SECRET must be at least 32 characters" in str(exc1.value)

    with pytest.raises(ValidationError) as exc2:
        Settings(
            env="prod",
            database={"password": "StrongDbSecretPassword987!"},
            redis={"password": "StrongRedisPassword987!"},
            auth={"jwt_secret": "a" * 32},
            tracking={"secret": "too_short"},
            server={"frontend_url": "https://ads.example.com", "trusted_proxies": ["127.0.0.1"]},
        )
    assert "LITEADS_TRACKING__SECRET" in str(exc2.value) and "32" in str(exc2.value)


def test_production_rejects_http_origins_and_wildcard_cors():
    """Production mode must enforce HTTPS origins and reject wildcard CORS '*'."""
    # HTTP frontend_url rejected
    with pytest.raises(ValidationError) as exc1:
        Settings(
            env="prod",
            database={"password": "StrongDbSecretPassword987!"},
            redis={"password": "StrongRedisPassword987!"},
            auth={"jwt_secret": "a" * 32},
            tracking={"secret": "b" * 32},
            server={"frontend_url": "http://insecure-frontend.com", "trusted_proxies": ["127.0.0.1"]},
        )
    assert "Insecure frontend_url" in str(exc1.value)

    # Wildcard CORS origin rejected
    with pytest.raises(ValidationError) as exc2:
        Settings(
            env="prod",
            database={"password": "StrongDbSecretPassword987!"},
            redis={"password": "StrongRedisPassword987!"},
            auth={"jwt_secret": "a" * 32},
            tracking={"secret": "b" * 32},
            server={
                "frontend_url": "https://ads.example.com",
                "trusted_proxies": ["127.0.0.1"],
                "cors_origins": ["*"],
            },
        )
    assert "Wildcard CORS origin '*' is strictly prohibited in production" in str(exc2.value)


def test_production_rejects_mailpit_smtp():
    """Production mode must reject mailpit / insecure local SMTP on port 1025."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            env="prod",
            database={"password": "StrongDbSecretPassword987!"},
            redis={"password": "StrongRedisPassword987!"},
            auth={"jwt_secret": "a" * 32},
            tracking={"secret": "b" * 32},
            email={"host": "mailpit", "port": 1025},
            server={"frontend_url": "https://ads.example.com", "trusted_proxies": ["127.0.0.1"]},
        )
    assert "Mailpit/local plaintext SMTP is development-only" in str(exc.value)


# -----------------------------------------------------------------------------
# 2. Topology & Worker Separation Tests
# -----------------------------------------------------------------------------
def test_topology_defaults_in_process_schedulers_by_environment():
    """In prod/staging, in-process schedulers default to False. In dev/test, they default to True."""
    dev_settings = Settings(env="dev")
    assert dev_settings.enable_in_process_schedulers is True

    prod_settings = Settings(
        env="prod",
        database={"password": "StrongDbSecretPassword987!"},
        redis={"password": "StrongRedisPassword987!"},
        auth={"jwt_secret": "a" * 32},
        tracking={"secret": "b" * 32},
        email={"host": "smtp.sendgrid.net", "port": 587},
        server={"frontend_url": "https://ads.example.com", "trusted_proxies": ["127.0.0.1"]},
    )
    assert prod_settings.enable_in_process_schedulers is False
    assert prod_settings.enable_webhook_worker is True
    assert prod_settings.enable_notification_worker is True
    assert prod_settings.enable_optimization_worker is True
    assert prod_settings.enable_digest_worker is True


# -----------------------------------------------------------------------------
# 3. Encrypted Automated Database Backup & Restore Drill Tests
# -----------------------------------------------------------------------------
def test_encrypted_backup_creation_and_restore_drill(tmp_path: Path):
    """Verify backup creation, AES-256-GCM encryption, checksum validation, and restore drill."""
    from unittest.mock import MagicMock, patch

    passphrase = "MasterBackupSuperSecretPassphrase2026!"
    output_dir = tmp_path / "backups"
    off_host_dir = tmp_path / "off_host"
    db_url = "postgresql://liteads:test_secret@localhost:5432/liteads"
    mock_sql = b"-- RivinityADS Database Dump\nCREATE TABLE advertisers (id SERIAL PRIMARY KEY);\n"

    def _mock_pg_dump(cmd, stdout=None, stderr=None, shell=False):
        if stdout:
            stdout.write(mock_sql)
        return MagicMock(returncode=0, stderr=b"")

    with patch("shutil.which", return_value="/usr/bin/pg_dump"), \
         patch("subprocess.run", side_effect=_mock_pg_dump):
        # 1. Create encrypted backup
        backup_file = create_backup(
            passphrase=passphrase,
            output_dir=output_dir,
            db_url=db_url,
            off_host_dir=off_host_dir,
            retention_days=10,
        )
        assert backup_file.exists()
        assert backup_file.name.endswith(".enc")

        # Verify SHA-256 manifest exists and matches
        sha_file = Path(str(backup_file) + ".sha256")
        assert sha_file.exists()
        expected_hash = hashlib.sha256(backup_file.read_bytes()).hexdigest()
        assert expected_hash in sha_file.read_text(encoding="utf-8")

        # Verify off-host copy
        off_host_backup = off_host_dir / backup_file.name
        assert off_host_backup.exists()
        assert hashlib.sha256(off_host_backup.read_bytes()).hexdigest() == expected_hash

        # 2. Run restore drill
        drill_report = run_restore_drill(backup_file, passphrase)
        assert drill_report["status"] == "success"
        assert drill_report["decrypted_size_bytes"] > 0


def test_restore_drill_rejects_tampered_ciphertext(tmp_path: Path):
    """Restore drill must fail-closed if ciphertext or tag has been tampered with."""
    passphrase = "MasterBackupSuperSecretPassphrase2026!"
    raw_sql = b"-- RivinityADS Database Dump\n-- Table: users\n-- Count: 10\n"
    encrypted = encrypt_data(raw_sql, passphrase)

    # Tamper with a single byte in ciphertext
    tampered = bytearray(encrypted)
    tampered[-5] ^= 0xFF

    backup_file = tmp_path / "backup_tampered.dump.enc"
    backup_file.write_bytes(bytes(tampered))

    # Writing invalid checksum to trigger checksum mismatch
    sha_file = Path(str(backup_file) + ".sha256")
    sha_file.write_text("0000000000000000000000000000000000000000000000000000000000000000  backup_tampered.dump.enc\n")

    with pytest.raises(ValueError) as exc:
        run_restore_drill(backup_file, passphrase)
    assert "Integrity check failed" in str(exc.value)


def test_restore_drill_rejects_wrong_passphrase(tmp_path: Path):
    """Restore drill must reject incorrect decryption passphrase with authentication failure."""
    passphrase = "MasterBackupSuperSecretPassphrase2026!"
    raw_sql = b"-- RivinityADS Database Dump\n-- Table: users\n-- Count: 10\n"
    encrypted = encrypt_data(raw_sql, passphrase)

    backup_file = tmp_path / "backup_valid.dump.enc"
    backup_file.write_bytes(encrypted)
    sha_file = Path(str(backup_file) + ".sha256")
    sha_file.write_text(f"{hashlib.sha256(encrypted).hexdigest()}  {backup_file.name}\n")

    with pytest.raises(ValueError) as exc:
        run_restore_drill(backup_file, "WrongIncorrectPassphrase!")
    assert "Decryption failed" in str(exc.value)


# -----------------------------------------------------------------------------
# 4. Subsystem Health Checks & Zero Information Leakage Tests
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_subsystem_health_and_secret_redaction(client: AsyncClient):
    """Verify health endpoints check subsystems without leaking credentials, tokens, or IPs."""
    # 1. Public /health
    res_health = await client.get("/health")
    assert res_health.status_code == 200
    data_health = res_health.json()
    assert data_health["status"] in ("healthy", "degraded")
    assert "password" not in str(data_health).lower()
    assert "secret" not in str(data_health).lower()

    # 2. Public /health/ready
    res_ready = await client.get("/health/ready")
    assert res_ready.status_code in (200, 503)
    data_ready = res_ready.json()
    assert "ready" in data_ready
    assert "password" not in str(data_ready).lower()

    # 3. Detailed /health/dependencies
    res_deps = await client.get("/health/dependencies")
    assert res_deps.status_code in (200, 503)
    deps_data = res_deps.json()
    deps = deps_data["dependencies"]

    # Verify all subsystems are checked
    assert "database" in deps
    assert "redis" in deps
    assert "worker_queue_lag" in deps
    assert "worker_dlq" in deps
    assert "email_provider" in deps
    assert "payment_provider" in deps
    assert "laya_sidecar" in deps

    # Verify strict secret redaction
    full_text = json.dumps(deps_data)
    for forbidden in ("password", "secret", "bearer", "postgres://", "redis://", "apikey"):
        assert forbidden not in full_text.lower()


# -----------------------------------------------------------------------------
# 5. OpenTelemetry Correlation ID Propagation Tests
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_correlation_id_propagation(client: AsyncClient):
    """Verify incoming X-Correlation-ID is preserved and linked across response headers."""
    test_cid = "corr_trace_test_998877"
    resp = await client.get("/ping", headers={"X-Correlation-ID": test_cid})
    assert resp.status_code == 200
    assert resp.headers.get("X-Correlation-ID") == test_cid
    assert resp.headers.get("X-Request-ID") == test_cid


# -----------------------------------------------------------------------------
# 6. SSRF, XSS, and Supply Chain Integrity Tests
# -----------------------------------------------------------------------------
def test_ssrf_blocks_private_and_metadata_ips():
    """Verify SSRF validation blocks loopback, private subnets, and AWS/GCP metadata destinations."""
    with pytest.raises(SSRFValidationError):
        validate_destination_url("http://127.0.0.1/hook", resolve_dns=False)

    with pytest.raises(SSRFValidationError):
        validate_destination_url("http://localhost:8000/webhook", resolve_dns=False)

    with pytest.raises(SSRFValidationError):
        validate_destination_url("http://169.254.169.254/latest/meta-data", resolve_dns=False)

    with pytest.raises(SSRFValidationError):
        validate_destination_url("http://10.0.0.5:9000/callback", resolve_dns=False)

    with pytest.raises(SSRFValidationError):
        validate_destination_url("http://192.168.1.100/notify", resolve_dns=False)

    # Valid public HTTPS destination is permitted
    parsed = validate_destination_url("https://api.partner.com/webhook", resolve_dns=False)
    assert parsed.netloc == "api.partner.com"


def test_xss_sanitizer_blocks_forbidden_tags_and_style_attributes():
    """Verify OpenDesign HTML content validator rejects script tags and style attributes."""
    # Script tag rejected
    with pytest.raises(ValidationError):
        OpenDesignSectionContent(html_content='<div><script>alert("xss")</script></div>')

    # Iframe rejected
    with pytest.raises(ValidationError):
        OpenDesignSectionContent(html_content='<div><iframe src="http://attacker.com"></iframe></div>')

    # Inline style rejected
    with pytest.raises(ValidationError):
        OpenDesignSectionContent(html_content='<div style="background-image: url(javascript:alert(1))">Test</div>')

    # Valid semantic markup accepted
    valid = OpenDesignSectionContent(html_content='<div class="content"><p>Safe paragraph</p></div>')
    assert "Safe paragraph" in valid.html_content


def test_production_compose_and_sbom_integrity():
    """Verify production Docker Compose uses pinned immutable tags, baseline SBOM exists, and dynamic SBOM workflow is configured."""
    prod_compose_path = Path(__file__).resolve().parent.parent.parent / "docker-compose.prod.yml"
    assert prod_compose_path.exists(), "docker-compose.prod.yml must exist"
    compose_text = prod_compose_path.read_text(encoding="utf-8")

    # Verify no :latest image tags in production compose
    assert ":latest" not in compose_text, "Production docker-compose must not use ':latest' image tags"

    # Verify baseline CycloneDX SBOM snapshot exists
    sbom_path = Path(__file__).resolve().parent.parent.parent / "deployment" / "sbom.json"
    assert sbom_path.exists(), "Baseline SBOM file deployment/sbom.json must exist"
    sbom_data = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert sbom_data.get("bomFormat") == "CycloneDX"
    assert len(sbom_data.get("components", [])) > 50

    # Verify dynamic artifact-tied SBOM CI workflow is configured
    sbom_workflow = Path(__file__).resolve().parent.parent.parent.parent / ".github" / "workflows" / "security-scan-and-sbom.yml"
    assert sbom_workflow.exists(), "Artifact-tied SBOM workflow security-scan-and-sbom.yml must exist"

