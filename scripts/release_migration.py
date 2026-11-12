#!/usr/bin/env python3
"""Database Migration Release Job Runner (Phase 7).

Executes database schema migrations as an explicit, isolated pre-deployment step.
Ensures API servers and background workers do not race to execute DDL migrations concurrently.
Supports:
  python scripts/release_migration.py --upgrade
  python scripts/release_migration.py --current
  python scripts/release_migration.py --rollback
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"


def run_alembic_command(args: list[str]) -> int:
    """Run alembic CLI command within backend directory."""
    cmd = [sys.executable, "-m", "alembic"] + args
    print(f"Executing migration command: {' '.join(cmd)}")
    env = os.environ.copy()
    proc = subprocess.run(cmd, cwd=str(BACKEND_DIR), env=env)
    return proc.returncode


def main():
    parser = argparse.ArgumentParser(description="Explicit database migration release runner")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--upgrade", action="store_true", help="Apply pending migrations up to head")
    group.add_argument("--rollback", action="store_true", help="Roll back exactly 1 revision (-1)")
    group.add_argument("--current", action="store_true", help="Print current database revision")
    group.add_argument("--history", action="store_true", help="Show migration revision history")

    args = parser.parse_args()

    if args.current:
        rc = run_alembic_command(["current"])
    elif args.history:
        rc = run_alembic_command(["history"])
    elif args.rollback:
        print("WARNING: Rolling back 1 database revision...")
        rc = run_alembic_command(["downgrade", "-1"])
        if rc == 0:
            print("Rollback completed successfully.")
            run_alembic_command(["current"])
    elif args.upgrade:
        print("Applying migrations up to head...")
        rc = run_alembic_command(["upgrade", "head"])
        if rc == 0:
            print("Database migration succeeded. Current revision:")
            run_alembic_command(["current"])
        else:
            print("CRITICAL: Database migration failed!", file=sys.stderr)

    sys.exit(rc)


if __name__ == "__main__":
    main()

