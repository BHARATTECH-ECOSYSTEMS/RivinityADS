#!/usr/bin/env bash
# Database Migration Runner Script (Phase 7)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$ROOT_DIR/backend"

ACTION="${1:-upgrade}"

cd "$BACKEND_DIR"

case "$ACTION" in
    upgrade)
        echo "==> Executing pre-release database migration..."
        python -m alembic upgrade head
        echo "==> Current database revision:"
        python -m alembic current
        ;;
    rollback)
        echo "==> Reverting 1 database revision..."
        python -m alembic downgrade -1
        echo "==> Current database revision:"
        python -m alembic current
        ;;
    current)
        python -m alembic current
        ;;
    *)
        echo "Usage: $0 [upgrade|rollback|current]"
        exit 1
        ;;
esac

