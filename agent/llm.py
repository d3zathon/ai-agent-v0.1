"""
Thin wrapper around the Ollama Python client, plus the JSON-schema tool
definitions the agent exposes to the model.

Kept deliberately small: ``OllamaClient.chat`` takes a plain list of
message dicts and returns a plain message dict, so it's easy to swap in a
fake client for tests (see tests/test_agent.py).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


class LLMConnectionError(RuntimeError):
    """Raised when the agent cannot reach the local Ollama server."""


# --- Tool schemas exposed to the model (Ollama / OpenAI-style function
# calling format) -------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {"name": "list_files", "description": "List files and subdirectories under a path inside the sandboxed workspace. Use '.' for the workspace root.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Path relative to the workspace root. Defaults to '.'."}}, "required": []}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read the full text contents of a file inside the workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Path to the file, relative to the workspace root."}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a text file inside the workspace. Parent directories are created automatically.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Path to the file, relative to the workspace root."}, "content": {"type": "string", "description": "Full text content to write to the file."}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run a single allowlisted shell-free command inside the workspace (python, python3, pytest, git, pip only). No shell chaining, redirection, or piping is permitted.", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "Full command line, e.g. 'pip install requests'."}, "timeout_seconds": {"type": "integer", "description": "Timeout in seconds (default 30)."}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "run_tests", "description": "Run pytest against a path inside the workspace and report whether the tests passed, plus full output.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Path to test, relative to the workspace root. Defaults to '.'."}, "timeout_seconds": {"type": "integer", "description": "Timeout in seconds (default 60)."}}, "required": []}}},
]


def _as_plain_dict(obj: Any) -> Dict[str, Any]:
    """Best-effort conversion of an ollama response object to a plain dict."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):  # pydantic-style object
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    raise TypeError(f"Cannot convert object of type {type(obj)} to dict")


def _tool_names(tools: List[Dict[str, Any]]) -> set[str]:
    """Return names of the tools actually exposed to the model."""
    names: set[str] = set()
    for tool in tools or []:
        function = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str) and name:
            names.add(name)
    return names


def _parse_json_text_tool_call(content: str, tools: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """Parse a conservative JSON-in-text tool request emitted by some local models."""
    allowed_names = _tool_names(tools)
    if not allowed_names or not content.strip():
        return None

    decoder = json.JSONDecoder()
    for start, char in enumerate(content):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue

        name = value.get("name")
        arguments = value.get("arguments", {})
        if not isinstance(name, str) or name not in allowed_names:
            continue
        if not isinstance(arguments, (dict, str)):
            continue

        return {
            "id": None,
            "function": {
                "name": name,
                "arguments": arguments,
            },
        }

    return None


class OllamaClient:
    """Minimal chat wrapper over the local ``ollama`` package."""

    def __init__(self, host: str, model: str):
        self.host = host
        self.model = model
        try:
            import ollama
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "The 'ollama' package is not installed. Run: pip install -r requirements.txt"
            ) from exc

        self._ollama = ollama
        self._client = ollama.Client(host=host)

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
        """
        Send the conversation to Ollama and return the assistant message as
        a plain dict: {"role": "assistant", "content": str, "tool_calls": [...]}.

        Native Ollama tool calls are preferred. If a model instead emits a
        JSON tool request in ordinary text, a validated fallback parser
        normalizes it into the same internal representation.
        """
        tools = tools or []
        try:
            response = self._client.chat(
                model=self.model,
                messages=messages,
                tools=tools,
            )
        except Exception as exc:  # noqa: BLE001 - surface a friendly error
            raise LLMConnectionError(
                f"Could not reach Ollama at {self.host} with model '{self.model}': {exc}"
            ) from exc

        response_dict = _as_plain_dict(response)
        message = _as_plain_dict(response_dict["message"])

        content = message.get("content") or ""
        raw_tool_calls = message.get("tool_calls") or []

        tool_calls: List[Dict[str, Any]] = []
        for call in raw_tool_calls:
            call = _as_plain_dict(call)
            function = _as_plain_dict(call.get("function", {}))
            tool_calls.append(
                {
                    "id": call.get("id"),
                    "function": {
                        "name": function.get("name"),
                        "arguments": function.get("arguments") or {},
                    },
                }
            )

        if not tool_calls:
            fallback_call = _parse_json_text_tool_call(content, tools)
            if fallback_call is not None:
                tool_calls = [fallback_call]
                content = ""

        return {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        }
