"""
CLI entrypoint for the local coding agent.

Usage:
    python -m agent.main                  # interactive REPL
    python -m agent.main "your task here" # run a single task and exit
"""

from __future__ import annotations

import functools
import json
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Allow running as `python agent/main.py` as well as `python -m agent.main`.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.llm import OllamaClient, TOOLS, LLMConnectionError  # noqa: E402
from agent.loop import AgentLoop, StepRecord  # noqa: E402
from agent.prompts import SYSTEM_PROMPT  # noqa: E402
from tools import filesystem, terminal, testing  # noqa: E402

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. Copy/create config/config.json."
        )
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    # Resolve workspace relative to the project root (not the cwd) so the
    # agent behaves the same no matter where it's launched from.
    workspace = Path(config.get("workspace", "./workspace"))
    if not workspace.is_absolute():
        workspace = (PROJECT_ROOT / workspace).resolve()
    config["_workspace_path"] = workspace

    config.setdefault("agent", {}).setdefault("max_steps", 12)
    config.setdefault("terminal", {}).setdefault("timeout_seconds", 30)
    config["terminal"].setdefault(
        "allowlist", ["python", "python3", "pytest", "git", "pip"]
    )

    return config


def build_tool_registry(config: Dict[str, Any]) -> Dict[str, Any]:
    workspace = config["_workspace_path"]
    term_cfg = config["terminal"]

    return {
        "list_files": functools.partial(filesystem.list_files, workspace),
        "read_file": functools.partial(filesystem.read_file, workspace),
        "write_file": functools.partial(filesystem.write_file, workspace),
        "run_command": functools.partial(
            terminal.run_command,
            workspace,
            allowlist=term_cfg["allowlist"],
            timeout_seconds=term_cfg.get("timeout_seconds", 30),
        ),
        "run_tests": functools.partial(testing.run_tests, workspace),
    }


def _print_step(record: StepRecord) -> None:
    if record.kind == "tool_call":
        print(f"\n[tool] {record.tool_name}({record.tool_args})")
        result = record.tool_result or {}
        summary = {k: v for k, v in result.items() if k not in ("stdout", "stderr", "content")}
        print(f"[result] {summary}")
        for key in ("stdout", "stderr", "content"):
            if result.get(key):
                snippet = str(result[key])
                if len(snippet) > 800:
                    snippet = snippet[:800] + "... (truncated)"
                print(f"[{key}]\n{snippet}")
    elif record.kind == "final":
        print("\n[agent]")
        print(record.content)


def build_agent(config: Dict[str, Any]) -> AgentLoop:
    llm = OllamaClient(
        host=config["ollama"]["host"],
        model=config["ollama"]["model"],
    )
    tool_registry = build_tool_registry(config)
    return AgentLoop(
        llm=llm,
        tools_schema=TOOLS,
        tool_registry=tool_registry,
        system_prompt=SYSTEM_PROMPT,
        max_steps=config["agent"]["max_steps"],
        on_step=_print_step,
    )


def run_repl(agent: AgentLoop) -> None:
    print("Security Agent v0.1 - local Ollama coding assistant")
    print("Type a task, or 'exit'/'quit' to stop.\n")
    while True:
        try:
            user_message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return

        if not user_message:
            continue
        if user_message.lower() in {"exit", "quit"}:
            print("Exiting.")
            return

        try:
            result = agent.run(user_message)
        except LLMConnectionError as exc:
            print(f"\n[error] {exc}")
            continue

        if result.hit_step_limit:
            print(f"\n(Reached max_steps={agent.max_steps} for this task.)")


def main() -> None:
    config = load_config()
    config["_workspace_path"].mkdir(parents=True, exist_ok=True)

    if config["ollama"]["model"] == "REPLACE_WITH_YOUR_MODEL":
        print(
            "warning: config/config.json still has the placeholder model name. "
            "Set 'ollama.model' to a model you have pulled, e.g. 'qwen2.5-coder:7b'.\n"
        )

    agent = build_agent(config)

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        try:
            result = agent.run(task)
        except LLMConnectionError as exc:
            print(f"[error] {exc}")
            sys.exit(1)
        if result.hit_step_limit:
            print(f"\n(Reached max_steps={agent.max_steps} for this task.)")
        return

    run_repl(agent)


if __name__ == "__main__":
    main()
