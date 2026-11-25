"""Comprehensive Test Suite for PostgreSQL Backup and Restore Workflow (P0-01).

Covers:
1. Missing database URL -> fail-closed with clear error, no partial artifacts created.
2. Missing pg_dump / psql binary -> fail-closed with clear error, no metadata fallback file created.
3. Non-zero subprocess exit -> non-zero exit & atomic cleanup (no partial file left behind).
4. Empty or truncated dump output -> non-zero exit & atomic cleanup.
5. Checksum mismatch -> fail-closed before decryption/restore.
6. Wrong passphrase / tampered ciphertext (GCM auth tag failure) -> fail-closed.
7. Production target guardrail -> refuses restore in prod or on prod host without override, succeeds with override.
8. Retention policy -> preserves the latest/last-known-good backup regardless of age.
9. Post-restore verification -> validates Alembic head and financial ledger consistency, rejects drift.
10. Real PostgreSQL 15 two-database integration test (executable against live PostgreSQL when configured).
"""

import gzip
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
from unittest.mock import MagicMock, patch
import pytest

from scripts.backup_db import (
    create_backup,
    encrypt_data,
    mask_db_url,
    normalize_db_url,
    prune_old_backups,
)
from scripts.restore_db import (
    check_production_guardrail,
    decrypt_data,
    execute_restore,
    run_restore,
    run_restore_drill,
    verify_checksum,
    verify_restored_database,
)

TEST_PASSPHRASE = "StrongTestPassphrase2026!With32CharsLong"
VALID_MOCK_SQL = b"""-- RivinityADS Database Dump
CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY);
INSERT INTO alembic_version (version_num) VALUES ('y4z5a6b7c8d9');

CREATE TABLE advertisers (id INTEGER PRIMARY KEY, name VARCHAR(255), balance NUMERIC(12, 4));
INSERT INTO advertisers (id, name, balance) VALUES (1, 'Acme Corp', 500.0000);

CREATE TABLE publishers (id INTEGER PRIMARY KEY, name VARCHAR(255));
INSERT INTO publishers (id, name) VALUES (1, 'Tech Blog');

CREATE TABLE campaigns (id INTEGER PRIMARY KEY, name VARCHAR(255), advertiser_id INTEGER);
INSERT INTO campaigns (id, name, advertiser_id) VALUES (101, 'Summer Promo', 1);

CREATE TABLE transactions (id INTEGER PRIMARY KEY, advertiser_id INTEGER, amount NUMERIC(12, 6));
INSERT INTO transactions (id, advertiser_id, amount) VALUES (1, 1, 500.000000);
"""


# -----------------------------------------------------------------------------
# 1. Database URL Resolution & Normalization Tests
# -----------------------------------------------------------------------------
def test_normalize_db_url_strips_driver_prefixes():
    assert normalize_db_url("postgresql+asyncpg://u:p@h:5432/d") == "postgresql://u:p@h:5432/d"
    assert normalize_db_url("postgresql+psycopg2://u:p@h:5432/d") == "postgresql://u:p@h:5432/d"
    assert normalize_db_url("postgres://u:p@h:5432/d") == "postgresql://u:p@h:5432/d"
    assert normalize_db_url("postgresql://u:p@h:5432/d") == "postgresql://u:p@h:5432/d"


def test_normalize_db_url_empty_raises():
    with pytest.raises(ValueError, match="Database URL cannot be empty"):
        normalize_db_url("")


def test_mask_db_url_masks_password():
    masked = mask_db_url("postgresql://user:super_secret_pw@db.internal:5432/liteads")
    assert "super_secret_pw" not in masked
    assert "user:***@db.internal:5432/liteads" in masked


# -----------------------------------------------------------------------------
# 2. Backup Creation Fail-Closed & Edge Cases
# -----------------------------------------------------------------------------
def test_backup_missing_db_url_fails(tmp_path: Path):
    """Missing database URL must raise ValueError and create no files."""
    output_dir = tmp_path / "backups"
    with pytest.raises(ValueError, match="Database URL must be provided"):
        create_backup(passphrase=TEST_PASSPHRASE, output_dir=output_dir, db_url=None)
    assert not output_dir.exists() or len(list(output_dir.iterdir())) == 0


def test_backup_missing_pg_dump_binary_fails(tmp_path: Path):
    """Missing pg_dump binary must fail immediately without falling back to fake metadata."""
    output_dir = tmp_path / "backups"
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="pg_dump binary not found in PATH"):
            create_backup(
                passphrase=TEST_PASSPHRASE,
                output_dir=output_dir,
                db_url="postgresql://liteads:test@localhost:5432/liteads",
            )
    assert not output_dir.exists() or len(list(output_dir.iterdir())) == 0


def test_backup_subprocess_error_atomic_cleanup(tmp_path: Path):
    """pg_dump non-zero exit must raise RuntimeError and leave no partial files."""
    output_dir = tmp_path / "backups"

    def _fail_pg_dump(cmd, stdout=None, stderr=None, shell=False):
        return MagicMock(returncode=1, stderr=b"pg_dump: error: connection to server failed: Connection refused")

    with patch("shutil.which", return_value="/usr/bin/pg_dump"), \
         patch("subprocess.run", side_effect=_fail_pg_dump):
        with pytest.raises(RuntimeError, match="pg_dump failed with exit code 1"):
            create_backup(
                passphrase=TEST_PASSPHRASE,
                output_dir=output_dir,
                db_url="postgresql://liteads:test@localhost:5432/liteads",
            )

    # Ensure no partial or temporary files remain in output directory
    created_files = list(output_dir.iterdir()) if output_dir.exists() else []
    assert len(created_files) == 0, f"Expected no leftover files, found: {created_files}"


def test_backup_empty_dump_fails(tmp_path: Path):
    """Empty dump (0 bytes) must raise RuntimeError and clean up atomically."""
    output_dir = tmp_path / "backups"

    def _empty_pg_dump(cmd, stdout=None, stderr=None, shell=False):
        return MagicMock(returncode=0, stderr=b"")

    with patch("shutil.which", return_value="/usr/bin/pg_dump"), \
         patch("subprocess.run", side_effect=_empty_pg_dump):
        with pytest.raises(RuntimeError, match="empty dump file"):
            create_backup(
                passphrase=TEST_PASSPHRASE,
                output_dir=output_dir,
                db_url="postgresql://liteads:test@localhost:5432/liteads",
            )

    created_files = list(output_dir.iterdir()) if output_dir.exists() else []
    assert len(created_files) == 0


def test_backup_success_with_encryption_and_manifest(tmp_path: Path):
    """Successful backup creates AES-GCM encrypted artifact and matching SHA-256 manifest."""
    output_dir = tmp_path / "backups"
    off_host_dir = tmp_path / "off_host"

    def _mock_pg_dump(cmd, stdout=None, stderr=None, shell=False):
        if stdout:
            stdout.write(VALID_MOCK_SQL)
        return MagicMock(returncode=0, stderr=b"")

    with patch("shutil.which", return_value="/usr/bin/pg_dump"), \
         patch("subprocess.run", side_effect=_mock_pg_dump):
        backup_file = create_backup(
            passphrase=TEST_PASSPHRASE,
            output_dir=output_dir,
            db_url="postgresql://liteads:test@localhost:5432/liteads",
            off_host_dir=off_host_dir,
            retention_days=30,
        )

    assert backup_file.exists()
    assert backup_file.name.endswith(".sql.gz.enc")

    # Verify SHA-256 manifest
    sha_file = Path(str(backup_file) + ".sha256")
    assert sha_file.exists()
    expected_hash = hashlib.sha256(backup_file.read_bytes()).hexdigest()
    assert expected_hash in sha_file.read_text(encoding="utf-8")

    # Verify off-host replica
    off_host_file = off_host_dir / backup_file.name
    assert off_host_file.exists()
    assert (off_host_dir / sha_file.name).exists()
    assert hashlib.sha256(off_host_file.read_bytes()).hexdigest() == expected_hash

    # Verify drill runner can decrypt payload
    drill_res = run_restore_drill(backup_file, TEST_PASSPHRASE)
    assert drill_res["status"] == "success"
    assert drill_res["decrypted_size_bytes"] == len(VALID_MOCK_SQL)


def test_backup_unencrypted_mode(tmp_path: Path):
    """Backup with encrypt=False produces valid unencrypted .sql.gz and .sha256."""
    output_dir = tmp_path / "backups"

    def _mock_pg_dump(cmd, stdout=None, stderr=None, shell=False):
        if stdout:
            stdout.write(VALID_MOCK_SQL)
        return MagicMock(returncode=0, stderr=b"")

    with patch("shutil.which", return_value="/usr/bin/pg_dump"), \
         patch("subprocess.run", side_effect=_mock_pg_dump):
        backup_file = create_backup(
            output_dir=output_dir,
            db_url="postgresql://liteads:test@localhost:5432/liteads",
            encrypt=False,
        )

    assert backup_file.exists()
    assert backup_file.name.endswith(".sql.gz")
    assert gzip.decompress(backup_file.read_bytes()) == VALID_MOCK_SQL


def test_backup_retention_preserves_keep_last(tmp_path: Path):
    """Retention cleanup must never delete newest backups guaranteed by keep_last."""
    backup_dir = tmp_path / "retention_test"
    backup_dir.mkdir(parents=True)

    # Create 3 synthetic backups with different timestamps
    file_old1 = backup_dir / "backup_20260101_100000.sql.gz.enc"
    file_old2 = backup_dir / "backup_20260102_100000.sql.gz.enc"
    file_newest = backup_dir / "backup_20261124_100000.sql.gz.enc"

    for f in (file_old1, file_old2, file_newest):
        f.write_bytes(b"dummy")
        Path(str(f) + ".sha256").write_text("dummy_hash  " + f.name)

    # Force old mtime on all files
    old_time = 100000.0
    os.utime(file_old1, (old_time, old_time))
    os.utime(file_old2, (old_time + 10, old_time + 10))
    os.utime(file_newest, (old_time + 20, old_time + 20))

    # With retention_days=0 and keep_last=2, the 2 newest must be kept, 1 oldest pruned
    pruned = prune_old_backups(backup_dir, retention_days=0, keep_last=2)
    assert pruned == 1
    assert not file_old1.exists()
    assert not Path(str(file_old1) + ".sha256").exists()
    assert file_old2.exists()
    assert file_newest.exists()


# -----------------------------------------------------------------------------
# 3. Restore Verification, Tampering & Decryption Tests
# -----------------------------------------------------------------------------
def test_restore_missing_checksum_manifest_fails(tmp_path: Path):
    """Restore must fail-closed if companion .sha256 manifest is missing."""
    backup_file = tmp_path / "backup_orphan.sql.gz.enc"
    backup_file.write_bytes(b"some_bytes")

    with pytest.raises(ValueError, match="Checksum manifest not found"):
        verify_checksum(backup_file)


def test_restore_checksum_mismatch_fails(tmp_path: Path):
    """Restore must fail-closed if checksum does not match manifest."""
    backup_file = tmp_path / "backup_tampered.sql.gz.enc"
    backup_file.write_bytes(b"actual_content")
    sha_file = Path(str(backup_file) + ".sha256")
    sha_file.write_text("0000000000000000000000000000000000000000000000000000000000000000  " + backup_file.name)

    with pytest.raises(ValueError, match="Integrity check failed"):
        verify_checksum(backup_file)


def test_restore_tampered_ciphertext_fails(tmp_path: Path):
    """Restore drill must reject tampered GCM ciphertext with authentication tag failure."""
    encrypted = encrypt_data(VALID_MOCK_SQL, TEST_PASSPHRASE)
    tampered = bytearray(encrypted)
    tampered[-5] ^= 0xFF  # Flip bit in GCM tag/ciphertext

    backup_file = tmp_path / "backup_tampered.sql.gz.enc"
    backup_file.write_bytes(bytes(tampered))
    sha_file = Path(str(backup_file) + ".sha256")
    sha_file.write_text(f"{hashlib.sha256(bytes(tampered)).hexdigest()}  {backup_file.name}\n")

    with pytest.raises(ValueError, match="Decryption failed"):
        run_restore_drill(backup_file, TEST_PASSPHRASE)


def test_restore_wrong_passphrase_fails(tmp_path: Path):
    """Restore drill must reject incorrect decryption passphrase with authentication failure."""
    encrypted = encrypt_data(VALID_MOCK_SQL, TEST_PASSPHRASE)
    backup_file = tmp_path / "backup_valid.sql.gz.enc"
    backup_file.write_bytes(encrypted)
    sha_file = Path(str(backup_file) + ".sha256")
    sha_file.write_text(f"{hashlib.sha256(encrypted).hexdigest()}  {backup_file.name}\n")

    with pytest.raises(ValueError, match="Decryption failed"):
        run_restore_drill(backup_file, "CompletelyWrongPassphrase!")


# -----------------------------------------------------------------------------
# 4. Production Target Guardrail Tests
# -----------------------------------------------------------------------------
def test_restore_production_guardrail_blocks_production():
    """Guardrail must block restore when environment or host indicates production."""
    # Test 1: Production environment variable
    with patch.dict(os.environ, {"LITEADS_ENV": "prod"}):
        with pytest.raises(PermissionError, match="Refusing to restore database in production"):
            check_production_guardrail("postgresql://user:pw@localhost:5432/ads", allow_production=False)

    # Test 2: Production hostname indicator
    with patch.dict(os.environ, {"LITEADS_ENV": "test"}):
        with pytest.raises(PermissionError, match="indicates a production database"):
            check_production_guardrail("postgresql://admin:pw@prod-db.bharattech.com:5432/liteads", allow_production=False)

    # Test 3: AWS RDS production hostname indicator
    with patch.dict(os.environ, {"LITEADS_ENV": "development"}):
        with pytest.raises(PermissionError, match="indicates a production database"):
            check_production_guardrail("postgresql://admin:pw@my-db.rds.amazonaws.com:5432/liteads", allow_production=False)


def test_restore_production_guardrail_allows_with_override():
    """Guardrail must permit restore when --allow-production-target is explicitly passed."""
    with patch.dict(os.environ, {"LITEADS_ENV": "prod"}):
        # Should not raise
        check_production_guardrail(
            "postgresql://admin:pw@prod-db.bharattech.com:5432/liteads",
            allow_production=True,
        )


# -----------------------------------------------------------------------------
# 5. Real Restore Execution & Binary Availability Tests
# -----------------------------------------------------------------------------
def test_restore_missing_psql_binary_fails():
    """Missing psql binary must fail closed with clear error."""
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="psql binary not found in PATH"):
            execute_restore("postgresql://user:pass@localhost:5432/db", b"CREATE TABLE x (id int);")


def test_restore_psql_subprocess_failure():
    """psql non-zero exit must raise RuntimeError and mask password."""
    def _fail_psql(cmd, input=None, capture_output=True, shell=False):
        return MagicMock(
            returncode=1,
            stderr=b"psql: error: FATAL: password authentication failed for user 'liteads':secret_pass",
        )

    with patch("shutil.which", return_value="/usr/bin/psql"), \
         patch("subprocess.run", side_effect=_fail_psql):
        with pytest.raises(RuntimeError) as exc_info:
            execute_restore("postgresql://liteads:secret_pass@localhost:5432/liteads", b"SELECT 1;")
        err_text = str(exc_info.value)
        assert "secret_pass" not in err_text
        assert ":***@" in err_text or "***" in err_text


# -----------------------------------------------------------------------------
# 6. Post-Restore Schema & Ledger Verification Tests (using SQLite for mocking)
# -----------------------------------------------------------------------------
def _setup_sqlite_db(db_path: Path, alembic_version: str = "y4z5a6b7c8d9", wallet: float = 500.0, ledger_sum: float = 500.0):
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
    if alembic_version:
        cursor.execute("INSERT INTO alembic_version VALUES (?)", (alembic_version,))
    cursor.execute("CREATE TABLE advertisers (id INTEGER PRIMARY KEY, balance NUMERIC(12, 4))")
    cursor.execute("INSERT INTO advertisers VALUES (1, ?)", (wallet,))
    cursor.execute("CREATE TABLE publishers (id INTEGER PRIMARY KEY)")
    cursor.execute("CREATE TABLE campaigns (id INTEGER PRIMARY KEY)")
    cursor.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, advertiser_id INTEGER, amount NUMERIC(12, 6))")
    cursor.execute("INSERT INTO transactions VALUES (1, 1, ?)", (ledger_sum,))
    conn.commit()
    conn.close()


def test_post_restore_verification_alembic_mismatch(tmp_path: Path):
    """Post-restore verification must fail if Alembic revision does not match expected head."""
    db_file = tmp_path / "test_mismatch.db"
    _setup_sqlite_db(db_file, alembic_version="wrong_old_version")

    with pytest.raises(ValueError, match="Alembic head mismatch"):
        verify_restored_database(f"sqlite:///{db_file}", expected_head="y4z5a6b7c8d9")


def test_post_restore_verification_ledger_drift_detected(tmp_path: Path):
    """Post-restore verification must fail if advertiser wallet balance drifts from ledger sum."""
    db_file = tmp_path / "test_drift.db"
    # Wallet is 500.00, but transactions sum is only 400.00 -> 100.00 drift!
    _setup_sqlite_db(db_file, wallet=500.0, ledger_sum=400.0)

    with pytest.raises(ValueError, match="Financial ledger drift detected"):
        verify_restored_database(f"sqlite:///{db_file}", expected_head="y4z5a6b7c8d9")


def test_post_restore_verification_success(tmp_path: Path):
    """Post-restore verification succeeds when Alembic head matches and ledger is balanced."""
    db_file = tmp_path / "test_ok.db"
    _setup_sqlite_db(db_file, wallet=500.0, ledger_sum=500.0)

    report = verify_restored_database(f"sqlite:///{db_file}", expected_head="y4z5a6b7c8d9")
    assert report["status"] == "success"
    assert report["alembic_versions"] == ["y4z5a6b7c8d9"]
    assert report["table_counts"]["advertisers"] == 1
    assert report["ledger_drift_free"] is True


# -----------------------------------------------------------------------------
# 7. Real PostgreSQL 15 Two-Database Live Integration Test
# -----------------------------------------------------------------------------
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Live PostgreSQL 15 two-database integration test requires TEST_DATABASE_URL environment variable",
)
def test_live_postgresql_two_database_integration(tmp_path: Path):
    """Live integration test: dumps db_source with real pg_dump, restores to db_target via psql."""
    source_url = os.environ["TEST_DATABASE_URL"]
    target_url = os.getenv("TEST_TARGET_DATABASE_URL", source_url + "_target")

    passphrase = "MasterIntegrationTestPassphrase2026!"
    output_dir = tmp_path / "live_backups"

    # Step 1: Dump source database with encryption
    backup_file = create_backup(
        passphrase=passphrase,
        output_dir=output_dir,
        db_url=source_url,
    )
    assert backup_file.exists()

    # Step 2: Restore into disposable target database and run full verification
    restore_result = run_restore(
        backup_file=backup_file,
        db_url=target_url,
        passphrase=passphrase,
        verify=True,
        allow_production=False,
        expected_head="y4z5a6b7c8d9",
    )
    assert restore_result["status"] == "success"
    assert restore_result["verification"]["ledger_drift_free"] is True
