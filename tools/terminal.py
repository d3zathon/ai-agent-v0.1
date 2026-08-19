"""
Restricted terminal execution tool.

Design goals:
  * Only a small allowlist of program names may be executed
    (python, python3, pytest, git, pip - configurable).
  * No shell is ever invoked (``shell=False``), so shell operators like
    ``&&``, ``;``, ``|``, backticks, and redirection cannot chain a second,
    disallowed command onto an allowed one.
  * Every invocation runs with a timeout and is confined to the workspace
    directory (``cwd``).
  * PowerShell, cmd.exe, bash, sh, and similar general-purpose shells are
    never on the allowlist and cannot be invoked through this tool.

This is a v0.1 guardrail, not a full sandbox: an allowed interpreter like
``python`` can still do a lot (e.g. ``python -c "..."``). Treat this tool
as "restricted", not "safe against a malicious model/user" - only run the
agent against local, lab-owned, or explicitly authorized targets.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_ALLOWLIST = ("python", "python3", "pytest", "git", "pip")

# Standalone tokens that would indicate shell-operator chaining if this
# command were ever run through a shell. subprocess.run(..., shell=False)
# already makes these inert (no shell parses them), but we reject them as
# a defense-in-depth / fail-clear measure - checked per-token *after*
# shlex parsing, so quoted content like `python -c "import time; ..."`
# (a semicolon inside a single argument) is not falsely flagged.
SHELL_OPERATOR_TOKENS = {"&&", "||", "|", ";", "`", ">", ">>", "<", "<<"}

MAX_OUTPUT_CHARS = 20_000


def _program_basename(program: str) -> str:
    name = Path(program).name.lower()
    if name.endswith(".exe"):
        name = name[: -len(".exe")]
    return name


def run_command(
    workspace: str | Path,
    command: str,
    allowlist: List[str] | tuple[str, ...] = DEFAULT_ALLOWLIST,
    timeout_seconds: int = 30,
) -> Dict[str, Any]:
    """
    Run ``command`` (a single command line, no shell) inside ``workspace``.

    Returns a dict with success/returncode/stdout/stderr, or
    success=False + error for rejected/failed-to-launch commands.
    """
    if not command or not command.strip():
        return {"success": False, "error": "Empty command."}

    if "\n" in command or "\r" in command:
        return {
            "success": False,
            "error": "Command rejected: multi-line commands are not permitted.",
        }

    try:
        parts = shlex.split(command, posix=True)
    except ValueError as exc:
        return {"success": False, "error": f"Could not parse command: {exc}"}

    if not parts:
        return {"success": False, "error": "Empty command."}

    for token in parts:
        if token in SHELL_OPERATOR_TOKENS or "$(" in token:
            return {
                "success": False,
                "error": (
                    f"Command rejected: disallowed token {token!r} found. "
                    "Shell chaining/redirection/substitution is not permitted."
                ),
            }

    program = _program_basename(parts[0])
    allowed = {_program_basename(p) for p in allowlist}
    if program not in allowed:
        return {
            "success": False,
            "error": (
                f"Command '{program}' is not on the allowlist "
                f"({sorted(allowed)}). Refusing to execute."
            ),
        }

    workspace_root = Path(workspace).expanduser().resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            parts,
            cwd=str(workspace_root),
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"Executable not found on PATH: {parts[0]}",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Command timed out after {timeout_seconds}s: {command}",
        }

    stdout = result.stdout[-MAX_OUTPUT_CHARS:]
    stderr = result.stderr[-MAX_OUTPUT_CHARS:]

    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "command": command,
    }
