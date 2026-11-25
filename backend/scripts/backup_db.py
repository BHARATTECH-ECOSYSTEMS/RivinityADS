#!/usr/bin/env python3
"""Automated Encrypted PostgreSQL Database Backup Service (Phase 7 / P0-01).

Features:
- Executes real PostgreSQL database dump using `pg_dump` binary.
- Normalizes connection strings (e.g. `postgresql+asyncpg://` -> `postgresql://`) and masks credentials in logs.
- Fail-closed: exits non-zero if `pg_dump` is missing, fails, or produces empty dump (no fake metadata fallback).
- Gzip compression and authenticated AES-256-GCM symmetric encryption (PBKDF2 key derivation).
- SHA-256 checksum manifest generation (.sha256).
- Atomic file generation: writes temporary artifacts and moves into final filenames only after success.
- Automated retention pruning with guardrail (never deletes newly created or last remaining backup).
- Off-host replica copy support.
"""

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

DEFAULT_BACKUP_DIR = Path("data/backups")
DEFAULT_RETENTION_DAYS = 30
KDF_ITERATIONS = 100_000


def normalize_db_url(url: str) -> str:
    """Normalize database URL for pg_dump/libpq by stripping driver prefixes."""
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


def encrypt_data(data: bytes, passphrase: str) -> bytes:
    """Compress and encrypt bytes using AES-256-GCM.
    
    Format: [16-byte Salt] + [12-byte Nonce] + [AES-GCM Ciphertext + Tag]
    """
    compressed = gzip.compress(data)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, compressed, None)
    return salt + nonce + ciphertext


def prune_old_backups(
    backup_dir: Path,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    keep_last: int = 1,
) -> int:
    """Delete backup files older than retention_days, ensuring at least keep_last backups remain."""
    if not backup_dir.exists():
        return 0
    cutoff_time = time.time() - (retention_days * 86400)

    # Collect backup archives (excluding checksum files and temporary files)
    backup_files = [
        f for f in backup_dir.iterdir()
        if f.is_file() and not f.name.endswith(".sha256") and not f.name.startswith(".tmp_")
        and ("backup_" in f.name)
    ]
    # Sort by mtime descending (newest first)
    backup_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    pruned_count = 0
    # Keep at least keep_last backups regardless of age
    candidates_to_prune = backup_files[keep_last:] if keep_last > 0 else backup_files
    for file_path in candidates_to_prune:
        if file_path.stat().st_mtime < cutoff_time:
            sha_file = Path(str(file_path) + ".sha256")
            file_path.unlink(missing_ok=True)
            sha_file.unlink(missing_ok=True)
            pruned_count += 1
            print(f"Pruned expired backup: {file_path.name}")

    return pruned_count


def create_backup(
    passphrase: str | None = None,
    output_dir: Path = DEFAULT_BACKUP_DIR,
    db_url: str | None = None,
    off_host_dir: Path | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    keep_last: int = 1,
    encrypt: bool = True,
) -> Path:
    """Execute real PostgreSQL database backup workflow with authenticated encryption."""
    if not db_url:
        raise ValueError("Database URL must be provided for database backup.")

    norm_url = normalize_db_url(db_url)
    masked_url = mask_db_url(norm_url)

    pg_dump_bin = shutil.which("pg_dump")
    if not pg_dump_bin:
        raise RuntimeError("pg_dump binary not found in PATH. Real PostgreSQL dump requires pg_dump.")

    if encrypt and not passphrase:
        raise ValueError("Passphrase must be provided when backup encryption is enabled.")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Final artifact paths
    artifact_ext = ".sql.gz.enc" if encrypt else ".sql.gz"
    final_backup_file = output_dir / f"backup_{timestamp}{artifact_ext}"
    final_sha_file = Path(str(final_backup_file) + ".sha256")

    # Atomic temporary paths
    temp_prefix = f".tmp_{timestamp}_{os.getpid()}"
    temp_raw_file = output_dir / f"{temp_prefix}.raw.sql"
    temp_backup_file = output_dir / f"{temp_prefix}.backup"
    temp_sha_file = output_dir / f"{temp_prefix}.sha256"

    print(f"Starting real database dump from {masked_url} using {pg_dump_bin}...")
    cmd = [pg_dump_bin, norm_url, "-F", "p", "--no-owner", "--no-privileges"]

    try:
        with open(temp_raw_file, "wb") as f_out:
            proc = subprocess.run(
                cmd,
                stdout=f_out,
                stderr=subprocess.PIPE,
                shell=False,
            )

        if proc.returncode != 0:
            raw_err = proc.stderr.decode("utf-8", errors="replace").strip()
            masked_err = mask_credentials(raw_err, norm_url)
            raise RuntimeError(f"pg_dump failed with exit code {proc.returncode}: {masked_err}")

        if not temp_raw_file.exists() or temp_raw_file.stat().st_size == 0:
            raise RuntimeError("pg_dump produced an empty dump file (0 bytes).")

        raw_size = temp_raw_file.stat().st_size
        raw_bytes = temp_raw_file.read_bytes()

        if encrypt:
            print(f"Compressing and encrypting {raw_size} bytes with AES-256-GCM...")
            assert passphrase is not None
            payload = encrypt_data(raw_bytes, passphrase)
        else:
            print(f"Compressing {raw_size} bytes with gzip...")
            payload = gzip.compress(raw_bytes)

        temp_backup_file.write_bytes(payload)

        # Compute SHA-256 Checksum Manifest
        checksum = hashlib.sha256(payload).hexdigest()
        temp_sha_file.write_text(f"{checksum}  {final_backup_file.name}\n", encoding="utf-8")

        # Atomic commit of artifacts
        os.replace(temp_backup_file, final_backup_file)
        os.replace(temp_sha_file, final_sha_file)
        print(f"Backup created atomically: {final_backup_file} (SHA256: {checksum[:16]}...)")

        # Replicate to off-host replica directory if configured
        if off_host_dir:
            off_host_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_backup_file, off_host_dir / final_backup_file.name)
            shutil.copy2(final_sha_file, off_host_dir / final_sha_file.name)
            print(f"Off-host copy replicated to {off_host_dir}")

        # Enforce retention policy safely after success
        prune_old_backups(output_dir, retention_days, keep_last)
        if off_host_dir:
            prune_old_backups(off_host_dir, retention_days, keep_last)

        return final_backup_file

    finally:
        # Atomic cleanup: ensure no temporary partial files are left behind
        for tmp_path in (temp_raw_file, temp_backup_file, temp_sha_file):
            if tmp_path.exists():
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Create real AES-256-GCM encrypted PostgreSQL database backup")
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL") or os.getenv("LITEADS_DATABASE__URL"),
        help="PostgreSQL connection string (defaults to DATABASE_URL or LITEADS_DATABASE__URL)",
    )
    parser.add_argument(
        "--backup-dir",
        "--output-dir",
        dest="output_dir",
        type=Path,
        default=DEFAULT_BACKUP_DIR,
        help="Destination directory for backup artifacts",
    )
    parser.add_argument(
        "--encrypt",
        action="store_true",
        default=True,
        help="Encrypt backup with AES-256-GCM (enabled by default)",
    )
    parser.add_argument(
        "--no-encrypt",
        action="store_false",
        dest="encrypt",
        help="Disable AES-256-GCM encryption (plain compressed dump)",
    )
    parser.add_argument(
        "--passphrase",
        default=os.getenv("BACKUP_PASSPHRASE") or os.getenv("BACKUP_ENCRYPTION_KEY"),
        help="Encryption passphrase (defaults to BACKUP_PASSPHRASE or BACKUP_ENCRYPTION_KEY)",
    )
    parser.add_argument(
        "--off-host-dir",
        type=Path,
        default=os.getenv("BACKUP_OFF_HOST_DIR"),
        help="Off-host replica directory",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help="Retention period in days",
    )
    parser.add_argument(
        "--keep-last",
        type=int,
        default=1,
        help="Minimum number of newest backups to keep regardless of age",
    )

    args = parser.parse_args()

    if not args.db_url:
        print(
            "ERROR: Database URL must be set via --db-url, DATABASE_URL, or LITEADS_DATABASE__URL env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.encrypt and not args.passphrase:
        print(
            "ERROR: Passphrase must be set via --passphrase, BACKUP_PASSPHRASE, or BACKUP_ENCRYPTION_KEY env var when encryption is enabled.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        create_backup(
            passphrase=args.passphrase,
            output_dir=args.output_dir,
            db_url=args.db_url,
            off_host_dir=args.off_host_dir,
            retention_days=args.retention_days,
            keep_last=args.keep_last,
            encrypt=args.encrypt,
        )
    except Exception as e:
        print(f"BACKUP FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
