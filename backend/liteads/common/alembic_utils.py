"""
Alembic migration utilities and head derivation (Phase 0).

Authoritative source of truth for inspecting the Alembic migration tree,
validating single-head integrity, and deriving the current head revision.
"""

import os
import sys
from pathlib import Path

# Prevent local directory shadowing of standard library modules (like email)
curr_dir = os.path.dirname(os.path.abspath(__file__))
if sys.path and sys.path[0] == curr_dir:
    sys.path.pop(0)
backend_dir = os.path.abspath(os.path.join(curr_dir, "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from alembic.config import Config
from alembic.script import ScriptDirectory


def find_alembic_ini(explicit_path: str | Path | None = None) -> Path:
    """Find the path to alembic.ini across repository directories."""
    if explicit_path is not None:
        p = Path(explicit_path).resolve()
        if p.exists():
            return p
        raise FileNotFoundError(f"Specified alembic.ini not found: {p}")

    candidates = [
        Path.cwd() / "backend" / "alembic.ini",
        Path.cwd() / "alembic.ini",
        Path(__file__).resolve().parent.parent.parent / "alembic.ini",
        Path(__file__).resolve().parent.parent.parent.parent / "backend" / "alembic.ini",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()

    raise FileNotFoundError("Could not locate alembic.ini in workspace candidates.")


def get_authoritative_alembic_heads(alembic_ini: str | Path | None = None) -> list[str]:
    """
    Get all migration heads from the Alembic migration tree using ScriptDirectory API.
    """
    ini_path = find_alembic_ini(alembic_ini)
    config = Config(str(ini_path))
    script = ScriptDirectory.from_config(config)
    return list(script.get_heads())


def get_authoritative_alembic_head(alembic_ini: str | Path | None = None) -> str:
    """
    Derive the single authoritative Alembic head revision from the migration graph.
    Validates that the Alembic graph has exactly one head.
    Raises ValueError if zero or multiple heads are detected.
    """
    heads = get_authoritative_alembic_heads(alembic_ini)
    if len(heads) == 0:
        raise ValueError("No Alembic migration heads found in migration tree!")
    if len(heads) > 1:
        raise ValueError(
            f"Multiple Alembic migration heads detected: {heads}. "
            "Migration graph must have exactly one head."
        )
    return heads[0]


def validate_single_head(alembic_ini: str | Path | None = None) -> str:
    """Validate that the Alembic graph has exactly one head and return it."""
    head = get_authoritative_alembic_head(alembic_ini)
    return head


if __name__ == "__main__":
    head = validate_single_head()
    print(head)
