#!/usr/bin/env python3
"""
Create an admin user for LiteAds.

The admin role has a unique constraint — only one admin can exist at a time.
Running this script when an admin already exists will display the existing
admin's email and exit without making changes.

Usage:
    python scripts/create_admin.py --name "Admin" --email admin@example.com --password "YourSecurePass1"

Or interactively (prompts for each field):
    python scripts/create_admin.py
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from liteads.common.config import get_settings  # noqa: E402
from liteads.common.database import db  # noqa: E402
from liteads.common.security import hash_password  # noqa: E402
from liteads.models import User  # noqa: E402
from liteads.models.base import Base  # noqa: E402


async def create_admin(name: str, email: str, password: str, force: bool = False) -> None:
    """Create a single admin user in the database."""
    from sqlalchemy import select

    settings = get_settings()

    print(f"\n  Database : {settings.database.host}:{settings.database.port}/{settings.database.name}")
    print(f"  Env      : {settings.env}\n")

    # Initialise the DB connection
    await db.init()

    # Ensure tables exist
    import liteads.models  # noqa: F401 — register metadata
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with db.session() as session:
        # Check for an existing admin
        existing = await session.scalar(
            select(User).where(User.role == "admin")
        )
        if existing is not None:
            if force:
                existing.email = email.lower()
                existing.name = name
                existing.password_hash = hash_password(password)
                existing.is_active = True
                existing.email_verified = True
                print(f"  [OK] Admin account updated successfully!")
                print(f"       Name  : {name}")
                print(f"       Email : {email.lower()}")
                print(f"       Role  : admin\n")
                print("  You can now log in at http://localhost:5173/login\n")
                await db.close()
                return
            print(f"  [!] An admin account already exists: {existing.email}")
            print("     Only one admin is allowed. Delete the existing admin first if you want to replace it (or pass --force).")
            await db.close()
            return

        # Check for duplicate email
        dup = await session.scalar(select(User).where(User.email == email.lower()))
        if dup is not None:
            print(f"  [X] Email '{email}' is already registered as a '{dup.role}' account.")
            await db.close()
            return

        # Create the admin user
        user = User(
            name=name,
            email=email.lower(),
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        # commit happens automatically via db.session() context manager

    print(f"  [OK] Admin account created successfully!")
    print(f"       Name  : {name}")
    print(f"       Email : {email.lower()}")
    print(f"       Role  : admin\n")
    print("  You can now log in at http://localhost:5173/login\n")

    await db.close()


def _prompt_field(label: str, default: str = "", secret: bool = False) -> str:
    """Prompt the user for a value, with optional default and masking."""
    suffix = f" [{default}]" if default else ""
    while True:
        if secret:
            value = getpass.getpass(f"  {label}{suffix}: ")
        else:
            value = input(f"  {label}{suffix}: ")
        value = value.strip() or default
        if value:
            return value
        print(f"    > {label} is required.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the LiteAds admin account",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  python scripts/create_admin.py --name Admin --email admin@liteads.io --password S3cureP@ss12",
    )
    parser.add_argument("--name", help="Display name for the admin")
    parser.add_argument("--email", help="Admin email address (used for login)")
    parser.add_argument("--password", help="Admin password (min 12 characters)")
    parser.add_argument("--force", action="store_true", help="Update existing admin credentials if one exists")
    args = parser.parse_args()

    print("\n+-------------------------------------+")
    print("|   LiteAds - Create Admin Account    |")
    print("+-------------------------------------+")

    name = args.name or _prompt_field("Name", default="Admin")
    email = args.email or _prompt_field("Email")
    password = args.password

    if not password:
        while True:
            password = _prompt_field("Password (min 12 chars)", secret=True)
            if len(password) < 12:
                print("    → Password must be at least 12 characters.")
                continue
            confirm = _prompt_field("Confirm password", secret=True)
            if password != confirm:
                print("    → Passwords do not match.")
                continue
            break
    elif len(password) < 12:
        print("\n  [X] Password must be at least 12 characters.\n")
        sys.exit(1)

    asyncio.run(create_admin(name, email, password, force=args.force))


if __name__ == "__main__":
    main()
