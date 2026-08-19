"""
Tests for the security-agent project itself (not for any particular LLM
output). These verify:

  * filesystem sandboxing (no path traversal / absolute-path escapes)
  * terminal allowlisting (only approved commands run, no shell chaining)
  * the test-runner tool
  * the agent loop's tool-calling/step-limit behavior, using a fake LLM
    so no real Ollama server is required to run this suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from agent.loop import AgentLoop
from tools import filesystem, terminal, testing


# ---------------------------------------------------------------------------
# Filesystem sandboxing
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def test_write_then_read_roundtrip(workspace):
    result = filesystem.write_file(workspace, "notes/todo.txt", "hello world")
    assert result["success"] is True

    read_result = filesystem.read_file(workspace, "notes/todo.txt")
    assert read_result["success"] is True
    assert read_result["content"] == "hello world"


def test_list_files(workspace):
    filesystem.write_file(workspace, "a.py", "print(1)")
    filesystem.write_file(workspace, "sub/b.py", "print(2)")

    result = filesystem.list_files(workspace, ".")
    assert result["success"] is True
    names = {entry["name"] for entry in result["entries"]}
    assert "a.py" in names
    assert "sub" in names


def test_new_source_file_warns_when_sibling_tests_exist(workspace):
    package = workspace / "demo_calculator"
    package.mkdir()
    (package / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    result = filesystem.write_file(
        workspace,
        "demo_calculator/demo_calculator.py",
        "def add(a, b):\n    return 0\n",
    )

    assert result["success"] is True
    assert "warning" in result
    assert "existing tests" in result["warning"]
    assert "tested implementation" in result["warning"]


def test_overwriting_existing_source_does_not_warn(workspace):
    package = workspace / "demo_calculator"
    package.mkdir()
    (package / "test_calculator.py").write_text("", encoding="utf-8")
    (package / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    result = filesystem.write_file(
        workspace,
        "demo_calculator/calculator.py",
        "def add(a, b):\n    return a - b\n",
    )

    assert result["success"] is True
    assert "warning" not in result


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.txt",
        "../../etc/passwd",
        "/etc/passwd",
        "C:\\Windows\\System32\\config",
        "\\\\server\\share\\file.txt",
    ],
)
def test_path_traversal_is_blocked(workspace, bad_path):
    read_result = filesystem.read_file(workspace, bad_path)
    assert read_result["success"] is False
    assert "error" in read_result

    write_result = filesystem.write_file(workspace, bad_path, "pwned")
    assert write_result["success"] is False


def test_read_missing_file_reports_error_not_exception(workspace):
    result = filesystem.read_file(workspace, "does_not_exist.txt")
    assert result["success"] is False
    assert "does not exist" in result["error"]


# ---------------------------------------------------------------------------
# Terminal allowlisting
# ---------------------------------------------------------------------------


def test_run_command_allows_allowlisted_program(workspace):
    # Use sys.executable directly so this test works regardless of whether
    # "python" resolves on PATH in the CI/dev environment.
    result = terminal.run_command(
        workspace,
        f"{sys.executable} --version",
        allowlist=["python", "python3", Path(sys.executable).name],
    )
    assert result["success"] is True
    assert "not on the allowlist" not in (result.get("error") or "")


def test_run_command_allows_quoted_semicolon_in_python_dash_c(workspace):
    # A semicolon *inside* a quoted -c argument is legitimate Python, not
    # shell chaining, and must not be rejected.
    result = terminal.run_command(
        workspace,
        f'{sys.executable} -c "import sys; sys.exit(0)"',
        allowlist=["python", "python3", Path(sys.executable).name],
    )
    assert result["success"] is True


def test_run_command_rejects_disallowed_program(workspace):
    result = terminal.run_command(workspace, "rm -rf /")
    assert result["success"] is False
    assert "allowlist" in result["error"]


def test_run_command_rejects_powershell_and_shells(workspace):
    for cmd in ["powershell -Command Get-Process", "cmd /c dir", "bash -c ls"]:
        result = terminal.run_command(workspace, cmd)
        assert result["success"] is False
        assert "allowlist" in result["error"]


def test_run_command_rejects_shell_chaining(workspace):
    result = terminal.run_command(workspace, "python --version && rm -rf /")
    assert result["success"] is False
    assert "disallowed token" in result["error"]


def test_run_command_respects_timeout(workspace):
    code = "import time; time.sleep(5)"
    result = terminal.run_command(
        workspace,
        f'{sys.executable} -c "{code}"',
        allowlist=["python", "python3", Path(sys.executable).name],
        timeout_seconds=1,
    )
    assert result["success"] is False
    assert "timed out" in result["error"]


# ---------------------------------------------------------------------------
# Test runner tool
# ---------------------------------------------------------------------------


def test_run_tests_detects_pass_and_fail(workspace):
    passing = workspace / "pkg_pass"
    passing.mkdir()
    (passing / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")

    failing = workspace / "pkg_fail"
    failing.mkdir()
    (failing / "test_bad.py").write_text("def test_bad():\n    assert 1 + 1 == 3\n")

    pass_result = testing.run_tests(workspace, "pkg_pass")
    assert pass_result["success"] is True
    assert pass_result["passed"] is True

    fail_result = testing.run_tests(workspace, "pkg_fail")
    assert fail_result["success"] is True
    assert fail_result["passed"] is False
    assert "assert" in fail_result["stdout"].lower()


# ---------------------------------------------------------------------------
# Agent loop (with a fake LLM - no real Ollama server needed)
# ---------------------------------------------------------------------------


class ScriptedLLM:
    """A fake LLM client that returns a pre-scripted sequence of messages."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if not self._script:
            return {"role": "assistant", "content": "(script exhausted)", "tool_calls": []}
        return self._script.pop(0)


def _tool_call(name, arguments):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "1", "function": {"name": name, "arguments": arguments}}],
    }


def _final(text):
    return {"role": "assistant", "content": text, "tool_calls": []}


def test_agent_loop_calls_tool_then_finishes():
    calls_seen = []

    def fake_write_file(path, content):
        calls_seen.append((path, content))
        return {"success": True, "path": path}

    llm = ScriptedLLM(
        [
            _tool_call("write_file", {"path": "x.py", "content": "print(1)"}),
            _final("Done, wrote x.py."),
        ]
    )

    loop = AgentLoop(
        llm=llm,
        tools_schema=[],
        tool_registry={"write_file": fake_write_file},
        system_prompt="test system prompt",
        max_steps=5,
    )

    result = loop.run("please write x.py")

    assert result.hit_step_limit is False
    assert result.final_message == "Done, wrote x.py."
    assert calls_seen == [("x.py", "print(1)")]


def test_agent_loop_stops_at_max_steps():
    """If the model never stops requesting tools, the loop must not run forever."""

    def fake_tool(**kwargs):
        return {"success": True}

    # Exactly max_steps tool-call turns, then one final turn for the
    # loop's forced end-of-budget summary request.
    llm = ScriptedLLM([_tool_call("noop", {}) for _ in range(3)] + [_final("forced summary")])

    loop = AgentLoop(
        llm=llm,
        tools_schema=[],
        tool_registry={"noop": fake_tool},
        system_prompt="test system prompt",
        max_steps=3,
    )

    result = loop.run("do something forever")

    assert result.hit_step_limit is True
    # 3 tool-call steps + 1 forced final summary step
    assert llm.calls == 4
    assert result.final_message == "forced summary"


def test_agent_loop_handles_unknown_tool_gracefully():
    llm = ScriptedLLM(
        [
            _tool_call("does_not_exist", {}),
            _final("I noticed that tool doesn't exist."),
        ]
    )
    loop = AgentLoop(
        llm=llm,
        tools_schema=[],
        tool_registry={},
        system_prompt="test system prompt",
        max_steps=5,
    )

    result = loop.run("try a bogus tool")
    assert result.hit_step_limit is False
    tool_step = result.steps[0]
    assert tool_step.tool_result["success"] is False
    assert "Unknown tool" in tool_step.tool_result["error"]


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_load_config_reads_expected_shape():
    from agent.main import load_config

    config = load_config()
    assert "ollama" in config
    assert "host" in config["ollama"]
    assert "model" in config["ollama"]
    assert config["agent"]["max_steps"] == 12
    assert config["_workspace_path"].name == "workspace"
