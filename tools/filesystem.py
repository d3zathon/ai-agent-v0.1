"""
Filesystem tools, hard-restricted to a single workspace directory.

Every public function in this module takes a path that is interpreted as
RELATIVE TO THE WORKSPACE ROOT. Absolute paths, drive letters, and ``..``
traversal that would escape the workspace are all rejected before any I/O
happens.

These functions never raise for "expected" failures (bad path, missing
file, etc.) - they return a dict with ``"success": False`` and an
``"error"`` message instead, so the agent loop can feed the failure back
to the LLM as a normal tool result.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

# Files larger than this are refused for read/write via the agent tools,
# to keep the LLM context small and avoid accidental huge dumps.
MAX_FILE_BYTES = 300_000


class WorkspaceViolation(Exception):
    """Raised internally when a path would escape the sandbox."""


def _normalize_workspace_root(workspace: str | Path) -> Path:
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_workspace_path(workspace: str | Path, relative_path: str) -> Path:
    """
    Resolve ``relative_path`` against ``workspace`` and guarantee the
    result is inside the workspace. Raises ``WorkspaceViolation`` if not.
    """
    root = _normalize_workspace_root(workspace)

    if relative_path is None:
        relative_path = "."

    # Reject obvious absolute-path / drive-letter escapes up front. This
    # covers POSIX absolute paths (/etc/passwd), Windows drive paths
    # (C:\Windows), and UNC paths (\\server\share).
    candidate = str(relative_path).strip()
    if candidate.startswith(("/", "\\")) or (len(candidate) > 1 and candidate[1] == ":"):
        raise WorkspaceViolation(
            f"Absolute paths are not allowed: {relative_path!r}. "
            "Use a path relative to the workspace root."
        )

    combined = (root / candidate).resolve()

    try:
        combined.relative_to(root)
    except ValueError:
        raise WorkspaceViolation(
            f"Path escapes the workspace sandbox: {relative_path!r}"
        ) from None

    return combined


def list_files(workspace: str | Path, path: str = ".") -> Dict[str, Any]:
    """List files and directories under ``path`` (relative to workspace)."""
    try:
        target = resolve_workspace_path(workspace, path)
    except WorkspaceViolation as exc:
        return {"success": False, "error": str(exc)}

    if not target.exists():
        return {"success": False, "error": f"Path does not exist: {path}"}
    if not target.is_dir():
        return {"success": False, "error": f"Path is not a directory: {path}"}

    entries: List[Dict[str, Any]] = []
    for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        entries.append(
            {
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
            }
        )

    return {"success": True, "path": path, "entries": entries}


def read_file(workspace: str | Path, path: str) -> Dict[str, Any]:
    """Read a text file's contents from the workspace."""
    try:
        target = resolve_workspace_path(workspace, path)
    except WorkspaceViolation as exc:
        return {"success": False, "error": str(exc)}

    if not target.exists():
        return {"success": False, "error": f"File does not exist: {path}"}
    if not target.is_file():
        return {"success": False, "error": f"Path is not a file: {path}"}

    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        return {
            "success": False,
            "error": f"File too large ({size} bytes > {MAX_FILE_BYTES} limit): {path}",
        }

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"success": False, "error": f"File is not valid UTF-8 text: {path}"}

    return {"success": True, "path": path, "content": content}


def write_file(workspace: str | Path, path: str, content: str) -> Dict[str, Any]:
    """Create or overwrite a text file inside the workspace."""
    try:
        target = resolve_workspace_path(workspace, path)
    except WorkspaceViolation as exc:
        return {"success": False, "error": str(exc)}

    if content is None:
        content = ""

    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        return {
            "success": False,
            "error": f"Refusing to write {len(encoded)} bytes (> {MAX_FILE_BYTES} limit): {path}",
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    return {
        "success": True,
        "path": path,
        "bytes_written": len(encoded),
        "message": f"Wrote {len(encoded)} bytes to {path}",
    }
