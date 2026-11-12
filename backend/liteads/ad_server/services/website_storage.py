"""Tenant-scoped project filesystem storage service.

Provides isolated, deterministic file storage for website projects.
Derives all tenant access from backend-verified ownership records.
Enforces strict path canonicalization, symlink escape rejection,
file-type allowlists, and per-project storage quotas.
Internal server filesystem paths are NEVER exposed to clients.
"""

from __future__ import annotations

from datetime import datetime, timezone
import mimetypes
import os
from pathlib import Path
import shutil
from typing import Any

from liteads.common.logger import get_logger

logger = get_logger(__name__)

# Allowed file extensions for website assets
ALLOWED_EXTENSIONS: set[str] = {
    ".html", ".htm",
    ".css",
    ".js",
    ".json",
    ".svg",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
    ".txt", ".md",
    ".woff", ".woff2", ".ttf", ".eot",
}

# 5 MB max per file
MAX_FILE_SIZE_BYTES: int = 5 * 1024 * 1024

# 50 MB total quota per website project
MAX_PROJECT_QUOTA_BYTES: int = 50 * 1024 * 1024


class StorageError(Exception):
    """Base exception for website storage operations."""


class PathTraversalError(StorageError):
    """Raised when an attempted path escapes the tenant's project boundary."""


class SymlinkEscapeError(StorageError):
    """Raised when a symlink points outside the authorized project root."""


class StorageQuotaExceededError(StorageError):
    """Raised when a file or project exceeds its allocation quota."""


class InvalidFileTypeError(StorageError):
    """Raised when a file extension or MIME type is not permitted."""


class StorageFileNotFoundError(StorageError):
    """Raised when a requested file does not exist."""


class WebsiteStorageService:
    """Manages tenant-scoped, project-isolated filesystem storage."""

    def __init__(self, base_path: str | Path | None = None) -> None:
        if base_path is not None:
            self.base_dir = Path(base_path).resolve()
        else:
            env_path = os.getenv("WEBSITE_STORAGE_PATH")
            if env_path:
                self.base_dir = Path(env_path).resolve()
            else:
                # Default: backend/data/websites
                self.base_dir = (
                    Path(__file__).resolve().parent.parent.parent.parent / "data" / "websites"
                ).resolve()

        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_project_dir(self, owner_user_id: int, project_id: int) -> Path:
        """Resolve and canonicalize the dedicated root directory for a project."""
        if not isinstance(owner_user_id, int) or owner_user_id <= 0:
            raise ValueError(f"Invalid owner_user_id: {owner_user_id}")
        if not isinstance(project_id, int) or project_id <= 0:
            raise ValueError(f"Invalid project_id: {project_id}")

        p_dir = (self.base_dir / str(owner_user_id) / str(project_id)).resolve()
        if not p_dir.is_relative_to(self.base_dir):
            raise PathTraversalError("Project directory resolution escaped storage base.")
        return p_dir

    def resolve_safe_path(
        self, owner_user_id: int, project_id: int, relative_path: str, must_exist: bool = False
    ) -> Path:
        """
        Resolve a relative path within the project directory.

        Enforces:
        - Rejection of null bytes
        - Rejection of leading slashes and drive letters
        - Rejection of traversal components ('..')
        - Canonicalization boundary check via is_relative_to
        - Symlink escape verification across all directory levels
        - Extension allowlist verification
        """
        project_root = self.get_project_dir(owner_user_id, project_id)

        if not relative_path:
            raise PathTraversalError("Path cannot be empty.")

        if "\0" in relative_path:
            raise PathTraversalError("Null byte detected in file path.")

        # Normalize slashes and strip leading slashes/drive specs
        clean_path = relative_path.replace("\\", "/").strip().lstrip("/")
        if not clean_path:
            raise PathTraversalError("Path cannot resolve to empty root.")

        # Verify no directory component is '..'
        parts = [p for p in clean_path.split("/") if p]
        if ".." in parts or "." in parts:
            raise PathTraversalError(f"Path traversal sequence detected in '{relative_path}'.")

        # Extension check
        suffix = Path(clean_path).suffix.lower()
        if not suffix or suffix not in ALLOWED_EXTENSIONS:
            raise InvalidFileTypeError(
                f"File extension '{suffix}' is not permitted. "
                f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )

        # Build absolute candidate path
        target_path = (project_root / clean_path).resolve()

        # Strict boundary check against canonical project root
        if not target_path.is_relative_to(project_root):
            raise PathTraversalError(
                f"Resolved path escaped project root: '{relative_path}'."
            )

        # Symlink escape verification: check every parent directory level
        current = target_path
        while current != project_root:
            if current.is_symlink():
                resolved_symlink = current.resolve()
                if not resolved_symlink.is_relative_to(project_root):
                    raise SymlinkEscapeError(
                        f"Symlink at '{current.name}' points outside project root."
                    )
            current = current.parent

        if must_exist and not target_path.exists():
            raise StorageFileNotFoundError(f"File not found: '{clean_path}'.")

        return target_path

    def get_project_total_size(self, owner_user_id: int, project_id: int) -> int:
        """Calculate total storage consumed by all files in a project."""
        project_root = self.get_project_dir(owner_user_id, project_id)
        if not project_root.exists():
            return 0

        total_bytes = 0
        for entry in project_root.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total_bytes += entry.stat().st_size
                except OSError:
                    pass
        return total_bytes

    def get_project_usage(self, owner_user_id: int, project_id: int) -> dict[str, Any]:
        """Return usage summary for a project."""
        project_root = self.get_project_dir(owner_user_id, project_id)
        if not project_root.exists():
            return {
                "project_id": project_id,
                "total_size_bytes": 0,
                "file_count": 0,
                "quota_bytes": MAX_PROJECT_QUOTA_BYTES,
            }

        total_bytes = 0
        file_count = 0
        for entry in project_root.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total_bytes += entry.stat().st_size
                    file_count += 1
                except OSError:
                    pass

        return {
            "project_id": project_id,
            "total_size_bytes": total_bytes,
            "file_count": file_count,
            "quota_bytes": MAX_PROJECT_QUOTA_BYTES,
        }

    def save_file(
        self, owner_user_id: int, project_id: int, relative_path: str, content: bytes
    ) -> dict[str, Any]:
        """
        Store a file in the project's isolated directory.

        Returns safe metadata containing only project-relative paths.
        """
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise StorageQuotaExceededError(
                f"File size ({len(content)} bytes) exceeds the maximum allowed ({MAX_FILE_SIZE_BYTES} bytes)."
            )

        target_path = self.resolve_safe_path(
            owner_user_id, project_id, relative_path, must_exist=False
        )

        # Quota accounting
        current_total = self.get_project_total_size(owner_user_id, project_id)
        existing_size = target_path.stat().st_size if target_path.exists() else 0
        new_total = current_total - existing_size + len(content)

        if new_total > MAX_PROJECT_QUOTA_BYTES:
            raise StorageQuotaExceededError(
                f"Project quota exceeded: operation requires {new_total} bytes, quota is {MAX_PROJECT_QUOTA_BYTES} bytes."
            )

        # Create parent directories safely
        target_path.parent.mkdir(parents=True, exist_ok=True)

        target_path.write_bytes(content)

        clean_rel = target_path.relative_to(self.get_project_dir(owner_user_id, project_id)).as_posix()
        mime_type, _ = mimetypes.guess_type(str(target_path))

        return {
            "path": clean_rel,
            "size_bytes": len(content),
            "content_type": mime_type or "application/octet-stream",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def read_file(
        self, owner_user_id: int, project_id: int, relative_path: str
    ) -> tuple[bytes, str]:
        """Read a stored file and return (bytes, content_type)."""
        target_path = self.resolve_safe_path(
            owner_user_id, project_id, relative_path, must_exist=True
        )
        if not target_path.is_file():
            raise StorageFileNotFoundError(f"Target '{relative_path}' is not a regular file.")

        content = target_path.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(target_path))
        return content, mime_type or "application/octet-stream"

    def delete_file(
        self, owner_user_id: int, project_id: int, relative_path: str
    ) -> bool:
        """Delete a file from the project directory. Returns True if deleted."""
        try:
            target_path = self.resolve_safe_path(
                owner_user_id, project_id, relative_path, must_exist=True
            )
        except StorageFileNotFoundError:
            return False

        if target_path.is_file():
            target_path.unlink()
            return True
        return False

    def list_files(
        self, owner_user_id: int, project_id: int
    ) -> list[dict[str, Any]]:
        """List all files in the project storage directory with safe relative paths."""
        project_root = self.get_project_dir(owner_user_id, project_id)
        if not project_root.exists():
            return []

        results: list[dict[str, Any]] = []
        for entry in project_root.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    stat = entry.stat()
                    rel = entry.relative_to(project_root).as_posix()
                    mime_type, _ = mimetypes.guess_type(str(entry))
                    results.append({
                        "path": rel,
                        "size_bytes": stat.st_size,
                        "content_type": mime_type or "application/octet-stream",
                        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    })
                except OSError:
                    continue

        results.sort(key=lambda x: x["path"])
        return results

    def delete_project_storage(self, owner_user_id: int, project_id: int) -> bool:
        """Purge all stored assets for a project."""
        project_root = self.get_project_dir(owner_user_id, project_id)
        if project_root.exists() and project_root.is_dir():
            shutil.rmtree(project_root, ignore_errors=True)
            return True
        return False


website_storage = WebsiteStorageService()

