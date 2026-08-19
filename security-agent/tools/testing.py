"""
Test execution tool: runs pytest against a path inside the workspace.

Kept separate from ``tools.terminal`` so the agent has one clearly-named,
purpose-built tool for "did my change work" - and so this specific path
never depends on free-form command parsing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from tools.filesystem import WorkspaceViolation, resolve_workspace_path

MAX_OUTPUT_CHARS = 20_000


def run_tests(
    workspace: str | Path,
    path: str = ".",
    timeout_seconds: int = 60,
) -> Dict[str, Any]:
    """
    Run ``pytest`` against ``path`` (relative to the workspace).

    Uses ``sys.executable -m pytest`` so the exact same Python
    interpreter/venv running the agent is used to run the tests.
    """
    try:
        target = resolve_workspace_path(workspace, path)
    except WorkspaceViolation as exc:
        return {"success": False, "passed": False, "error": str(exc)}

    if not target.exists():
        return {
            "success": False,
            "passed": False,
            "error": f"Path does not exist: {path}",
        }

    workspace_root = Path(workspace).expanduser().resolve()

    cmd = [sys.executable, "-m", "pytest", str(target), "-v"]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(workspace_root),
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "passed": False,
            "error": f"Tests timed out after {timeout_seconds}s.",
        }

    stdout = result.stdout[-MAX_OUTPUT_CHARS:]
    stderr = result.stderr[-MAX_OUTPUT_CHARS:]

    # pytest exit code 0 = all tests passed, 5 = no tests collected.
    passed = result.returncode == 0

    return {
        "success": True,
        "passed": passed,
        "returncode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "path": path,
    }
