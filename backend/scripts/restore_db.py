#!/usr/bin/env python3
"""Database Restore and Disaster Recovery Drill Runner (Phase 7 / P0-01).

Features:
- Verifies SHA-256 checksum manifest before attempting decryption (fail-closed on tampering or missing manifest).
- Decrypts AES-256-GCM ciphertext using PBKDF2-derived key and authenticates GCM tag.
- Decompresses gzip stream.
- Production target guardrail: refuses restore into production environment or hosts without explicit `--allow-production-target`.
- Executes real database restore into target PostgreSQL instance via `psql` (or `pg_restore` for custom format).
- Post-restore verification: connects to database, checks Alembic revision head `y4z5a6b7c8d9`, critical core tables, and verifies financial ledger balance reconciliation (zero drift).
- Disaster recovery drill mode: `--verify-only` performs integrity verification without touching the database.
"""

import argparse
import asyncio
import gzip
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse, urlunparse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

KDF_ITERATIONS = 100_000
DEFAULT_EXPECTED_HEAD = "y4z5a6b7c8d9"


def normalize_db_url(url: str) -> str:
    """Normalize database URL for psql/libpq by stripping driver prefixes."""
    if not url:
        raise ValueError("Database URL cannot be empty.")
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://"):]
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url[len("postgresql+psycopg2://"):]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def mask_db_url(url: str) -> str:
    """Mask password in database URL for safe logging and error reporting."""
    try:
        parsed = urlparse(url)
        if parsed.password:
            netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
            return urlunparse(parsed._replace(netloc=netloc))
        return url
    except Exception:
        return re.sub(r":([^/@]+)@", ":***@", url)


def mask_credentials(text: str, db_url: str | None = None) -> str:
    """Mask credentials and database passwords in text or error messages."""
    masked = re.sub(r":([^/@]+)@", ":***@", text)
    if db_url:
        try:
            parsed = urlparse(db_url)
            if parsed.password:
                masked = masked.replace(parsed.password, "***")
        except Exception:
            pass
    return masked


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive 256-bit AES key from passphrase using PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def decrypt_data(encrypted_bytes: bytes, passphrase: str) -> bytes:
    """Decrypt AES-256-GCM payload and decompress gzip data.
    
    Format: [16-byte Salt] + [12-byte Nonce] + [AES-GCM Ciphertext + Tag]
    """
    if len(encrypted_bytes) < 28:
        raise ValueError("Invalid encrypted backup archive: header is truncated.")

    salt = encrypted_bytes[:16]
    nonce = encrypted_bytes[16:28]
    ciphertext = encrypted_bytes[28:]

    key = derive_key(passphrase, salt)
    aesgcm = AESGCM(key)

    try:
        compressed = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as e:
        raise ValueError(f"Decryption failed: authentication tag mismatch or invalid passphrase. ({e})") from e

    return gzip.decompress(compressed)


def verify_checksum(backup_file: Path) -> str:
    """Verify SHA-256 checksum against accompanying .sha256 manifest."""
    if not backup_file.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    manifest_candidates = [
        Path(str(backup_file) + ".sha256"),
        backup_file.with_name(backup_file.name + ".sha256"),
        backup_file.with_suffix(".sha256"),
    ]
    sha_file = None
    for cand in manifest_candidates:
        if cand.exists():
            sha_file = cand
            break

    if not sha_file:
        raise ValueError(f"Integrity check failed: Checksum manifest not found for {backup_file.name}.")

    expected_line = sha_file.read_text(encoding="utf-8").strip()
    expected_hash = expected_line.split()[0].lower()
    actual_hash = hashlib.sha256(backup_file.read_bytes()).hexdigest().lower()

    if expected_hash != actual_hash:
        raise ValueError(
            f"Integrity check failed! Expected checksum {expected_hash}, but computed {actual_hash}. "
            "Archive may be corrupted or tampered with."
        )

    print(f"Checksum verified: {actual_hash[:16]}... OK")
    return actual_hash


def check_production_guardrail(db_url: str, allow_production: bool = False) -> None:
    """Fail closed if target appears to be production without explicit override."""
    if allow_production:
        return

    env_name = (
        os.getenv("LITEADS_ENV", "") or 
        os.getenv("ENVIRONMENT", "") or 
        os.getenv("LITEADS_ENVIRONMENT", "")
    ).lower()

    if env_name in ("prod", "production"):
        raise PermissionError(
            "Refusing to restore database in production environment without explicit --allow-production-target flag."
        )

    try:
        parsed = urlparse(db_url)
        hostname = (parsed.hostname or "").lower()
        prod_indicators = ("prod", "production", "rds.amazonaws.com", "azure.com", "bharattech.com")
        if any(ind in hostname for ind in prod_indicators):
            raise PermissionError(
                f"Target database hostname '{hostname}' indicates a production database. "
                "Refusing restore without explicit --allow-production-target flag."
            )
    except Exception as e:
        if isinstance(e, PermissionError):
            raise


def execute_restore(db_url: str, sql_payload: bytes) -> None:
    """Execute real SQL restore into target PostgreSQL instance."""
    norm_url = normalize_db_url(db_url)
    masked_url = mask_db_url(norm_url)

    # Check format: custom format starts with PGDMP magic header
    if sql_payload.startswith(b"PGDMP"):
        pg_restore_bin = shutil.which("pg_restore")
        if not pg_restore_bin:
            raise RuntimeError("pg_restore binary not found in PATH. Real restore requires pg_restore.")
        print(f"Restoring custom format database archive into {masked_url}...")
        proc = subprocess.run(
            [pg_restore_bin, "--clean", "--if-exists", "--no-owner", "--no-privileges", "-d", norm_url],
            input=sql_payload,
            capture_output=True,
            shell=False,
        )
        if proc.returncode != 0:
            raw_err = proc.stderr.decode("utf-8", errors="replace").strip()
            masked_err = mask_credentials(raw_err, norm_url)
            raise RuntimeError(f"pg_restore failed with exit code {proc.returncode}: {masked_err}")
    else:
        psql_bin = shutil.which("psql")
        if not psql_bin:
            raise RuntimeError("psql binary not found in PATH. Real PostgreSQL restore requires psql.")
        print(f"Restoring plain SQL database dump ({len(sql_payload)} bytes) into {masked_url}...")
        proc = subprocess.run(
            [psql_bin, norm_url, "-v", "ON_ERROR_STOP=1"],
            input=sql_payload,
            capture_output=True,
            shell=False,
        )
        if proc.returncode != 0:
            raw_err = proc.stderr.decode("utf-8", errors="replace").strip()
            masked_err = mask_credentials(raw_err, norm_url)
            raise RuntimeError(f"psql restore failed with exit code {proc.returncode}: {masked_err}")

    print("Database restore executed successfully.")


def verify_restored_database(db_url: str, expected_head: str = DEFAULT_EXPECTED_HEAD) -> dict:
    """Connect to restored database and verify schema, Alembic head, and ledger consistency."""
    norm_url = normalize_db_url(db_url)

    # Support SQLite for unit testing environments
    if norm_url.startswith("sqlite"):
        import sqlite3
        db_path = norm_url.replace("sqlite:///", "").replace("sqlite://", "")
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT version_num FROM alembic_version")
            versions = [r[0] for r in cursor.fetchall()]
            if not versions:
                raise ValueError("Post-restore verification failed: alembic_version table is empty.")
            if expected_head and expected_head not in versions:
                raise ValueError(
                    f"Post-restore verification failed: Alembic head mismatch. Expected '{expected_head}', got {versions}."
                )

            critical_tables = ["advertisers", "publishers", "campaigns", "transactions"]
            table_counts = {}
            for tbl in critical_tables:
                cursor.execute(f"SELECT COUNT(*) FROM {tbl}")
                table_counts[tbl] = cursor.fetchone()[0]

            drift_query = """
                SELECT 
                    a.id AS advertiser_id,
                    a.balance AS wallet_balance,
                    COALESCE(SUM(t.amount), 0) AS calculated_ledger_balance,
                    (a.balance - COALESCE(SUM(t.amount), 0)) AS drift
                FROM advertisers a
                LEFT JOIN transactions t ON a.id = t.advertiser_id
                GROUP BY a.id, a.balance
                HAVING (a.balance - COALESCE(SUM(t.amount), 0)) != 0
            """
            cursor.execute(drift_query)
            drift_rows = cursor.fetchall()
            if drift_rows:
                drift_details = [
                    f"advertiser {r[0]}: wallet={r[1]}, ledger_sum={r[2]}, drift={r[3]}"
                    for r in drift_rows
                ]
                raise ValueError(
                    f"Post-restore verification failed: Financial ledger drift detected! ({'; '.join(drift_details)})"
                )

            return {
                "status": "success",
                "alembic_versions": versions,
                "table_counts": table_counts,
                "ledger_drift_free": True,
            }
        finally:
            conn.close()

    # PostgreSQL verification using native asyncpg
    import asyncpg

    async def _verify_pg() -> dict:
        conn = await asyncpg.connect(norm_url)
        try:
            records = await conn.fetch("SELECT version_num FROM alembic_version")
            versions = [r["version_num"] for r in records]
            if not versions:
                raise ValueError("Post-restore verification failed: alembic_version table is empty.")
            if expected_head and expected_head not in versions:
                raise ValueError(
                    f"Post-restore verification failed: Alembic head mismatch. Expected '{expected_head}', got {versions}."
                )

            critical_tables = ["advertisers", "publishers", "campaigns", "transactions"]
            table_counts = {}
            for tbl in critical_tables:
                try:
                    cnt = await conn.fetchval(f"SELECT COUNT(*) FROM {tbl}")
                    table_counts[tbl] = int(cnt or 0)
                except Exception as e:
                    raise ValueError(f"Post-restore verification failed: table '{tbl}' query failed: {e}")

            drift_query = """
                SELECT 
                    a.id AS advertiser_id,
                    a.balance AS wallet_balance,
                    COALESCE(SUM(t.amount), 0) AS calculated_ledger_balance,
                    (a.balance - COALESCE(SUM(t.amount), 0)) AS drift
                FROM advertisers a
                LEFT JOIN transactions t ON a.id = t.advertiser_id
                GROUP BY a.id, a.balance
                HAVING (a.balance - COALESCE(SUM(t.amount), 0)) != 0
            """
            drift_rows = await conn.fetch(drift_query)
            if drift_rows:
                drift_details = [
                    f"advertiser {r['advertiser_id']}: wallet={r['wallet_balance']}, ledger_sum={r['calculated_ledger_balance']}, drift={r['drift']}"
                    for r in drift_rows
                ]
                raise ValueError(
                    f"Post-restore verification failed: Financial ledger drift detected! ({'; '.join(drift_details)})"
                )

            return {
                "status": "success",
                "alembic_versions": versions,
                "table_counts": table_counts,
                "ledger_drift_free": True,
            }
        finally:
            await conn.close()

    return asyncio.run(_verify_pg())


def run_restore_drill(backup_file: Path, passphrase: str | None = None) -> dict:
    """Perform decryption, checksum verification, and test restore drill without modifying database."""
    print(f"Starting restore drill on {backup_file}...")

    # 1. Verify Checksum
    verify_checksum(backup_file)

    # 2. Decrypt & Decompress
    encrypted_bytes = backup_file.read_bytes()
    if backup_file.name.endswith(".enc"):
        if not passphrase:
            raise ValueError("Passphrase required to decrypt encrypted backup archive.")
        decrypted_bytes = decrypt_data(encrypted_bytes, passphrase)
    else:
        decrypted_bytes = gzip.decompress(encrypted_bytes)

    print(f"Successfully decrypted {len(decrypted_bytes)} bytes of uncompressed database payload.")

    # 3. Verify content signatures
    content_str = decrypted_bytes.decode("utf-8", errors="replace")
    if not any(sig in content_str for sig in ("CREATE TABLE", "INSERT INTO", "RivinityADS", "-- Table:")):
        raise ValueError("Restore validation failed: archive does not contain recognizable SQL dump signatures.")

    return {
        "status": "success",
        "verified_checksum": True,
        "decrypted_size_bytes": len(decrypted_bytes),
        "backup_file": str(backup_file),
    }


def run_restore(
    backup_file: Path,
    db_url: str,
    passphrase: str | None = None,
    verify: bool = True,
    allow_production: bool = False,
    expected_head: str = DEFAULT_EXPECTED_HEAD,
) -> dict:
    """Execute complete restore workflow including integrity check, restore, and verification."""
    # 1. Verify Checksum
    verify_checksum(backup_file)

    # 2. Decrypt & Decompress
    raw_bytes = backup_file.read_bytes()
    if backup_file.name.endswith(".enc"):
        if not passphrase:
            raise ValueError("Passphrase required to decrypt encrypted backup archive.")
        sql_payload = decrypt_data(raw_bytes, passphrase)
    else:
        sql_payload = gzip.decompress(raw_bytes)

    # 3. Guardrail check
    check_production_guardrail(db_url, allow_production=allow_production)

    # 4. Real Restore Execution
    execute_restore(db_url, sql_payload)

    # 5. Post-Restore Verification
    verification_report = None
    if verify:
        verification_report = verify_restored_database(db_url, expected_head=expected_head)
        print("POST-RESTORE VERIFICATION PASSED:")
        print(f"  Alembic Head(s): {verification_report['alembic_versions']}")
        print(f"  Table Counts: {verification_report['table_counts']}")
        print("  Financial Ledger: Balanced (Zero Drift)")

    return {
        "status": "success",
        "backup_file": str(backup_file),
        "target_db": mask_db_url(db_url),
        "verification": verification_report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore / verify encrypted PostgreSQL database backup")
    parser.add_argument("backup_file", nargs="?", default=None, type=Path, help="Path to backup archive file")
    parser.add_argument("--input", "-i", dest="input_file", type=Path, default=None, help="Path to backup archive file")
    parser.add_argument(
        "--db-url",
        default=os.getenv("RESTORE_DATABASE_URL") or os.getenv("DATABASE_URL") or os.getenv("LITEADS_DATABASE__URL"),
        help="Target database URL (defaults to RESTORE_DATABASE_URL, DATABASE_URL, or LITEADS_DATABASE__URL)",
    )
    parser.add_argument(
        "--passphrase",
        default=os.getenv("BACKUP_PASSPHRASE") or os.getenv("BACKUP_ENCRYPTION_KEY"),
        help="Decryption passphrase (defaults to BACKUP_PASSPHRASE or BACKUP_ENCRYPTION_KEY)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        default=True,
        help="Perform post-restore schema, table count, and ledger drift verification (default: True)",
    )
    parser.add_argument(
        "--no-verify",
        action="store_false",
        dest="verify",
        help="Skip post-restore verification",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Perform archive integrity and decryption drill without executing database restore",
    )
    parser.add_argument(
        "--allow-production-target",
        action="store_true",
        help="Explicitly permit restoring into production environment or target database",
    )
    parser.add_argument(
        "--expected-head",
        default=DEFAULT_EXPECTED_HEAD,
        help="Expected authoritative Alembic revision head (default: y4z5a6b7c8d9)",
    )

    args = parser.parse_args()
    target_file = args.input_file or args.backup_file

    if not target_file:
        print("ERROR: Backup file must be provided via positional argument or --input flag.", file=sys.stderr)
        sys.exit(1)

    if not target_file.exists():
        print(f"ERROR: Backup file not found: {target_file}", file=sys.stderr)
        sys.exit(1)

    if args.verify_only:
        try:
            run_restore_drill(target_file, args.passphrase)
            print("RESTORE DRILL PASSED: Database backup integrity and decryption verified.")
            sys.exit(0)
        except Exception as e:
            print(f"RESTORE DRILL FAILED: {e}", file=sys.stderr)
            sys.exit(1)

    if not args.db_url:
        print(
            "ERROR: Target database URL must be set via --db-url, RESTORE_DATABASE_URL, or DATABASE_URL env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        run_restore(
            backup_file=target_file,
            db_url=args.db_url,
            passphrase=args.passphrase,
            verify=args.verify,
            allow_production=args.allow_production_target,
            expected_head=args.expected_head,
        )
        print("DATABASE RESTORE COMPLETED SUCCESSFULLY.")
    except Exception as e:
        print(f"RESTORE FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
