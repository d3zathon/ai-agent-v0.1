"""
The core agent loop:

    User -> LLM -> Tool -> Result -> LLM -> ... -> Final answer

The loop is deliberately framework-free: it just repeatedly calls an LLM
client's ``.chat(messages, tools)`` method, executes any requested tool
calls through a small local registry, feeds the results back as
``role="tool"`` messages, and stops when the model responds with no more
tool calls (or the step budget runs out).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

ToolFn = Callable[..., Dict[str, Any]]


@dataclass
class StepRecord:
    """A record of one loop iteration, useful for logging/tests."""

    kind: str  # "tool_call" | "final"
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_result: Optional[Dict[str, Any]] = None
    content: Optional[str] = None


@dataclass
class AgentResult:
    final_message: str
    steps: List[StepRecord] = field(default_factory=list)
    hit_step_limit: bool = False


class AgentLoop:
    def __init__(
        self,
        llm: Any,
        tools_schema: List[Dict[str, Any]],
        tool_registry: Dict[str, ToolFn],
        system_prompt: str,
        max_steps: int = 12,
        on_step: Optional[Callable[[StepRecord], None]] = None,
    ):
        self.llm = llm
        self.tools_schema = tools_schema
        self.tool_registry = tool_registry
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.on_step = on_step or (lambda record: None)

    def _call_tool(self, name: str, arguments: Any) -> Dict[str, Any]:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                return {"success": False, "error": f"Could not parse tool arguments as JSON: {arguments!r}"}

        if not isinstance(arguments, dict):
            arguments = {}

        fn = self.tool_registry.get(name)
        if fn is None:
            return {"success": False, "error": f"Unknown tool: {name!r}"}

        try:
            return fn(**arguments)
        except TypeError as exc:
            return {"success": False, "error": f"Invalid arguments for tool '{name}': {exc}"}
        except Exception as exc:  # noqa: BLE001 - never let a tool crash the loop
            return {"success": False, "error": f"Tool '{name}' raised an exception: {exc}"}

    def run(self, user_message: str) -> AgentResult:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        steps: List[StepRecord] = []

        for step_index in range(self.max_steps):
            assistant_message = self.llm.chat(messages, tools=self.tools_schema)
            tool_calls = assistant_message.get("tool_calls") or []

            if not tool_calls:
                content = assistant_message.get("content") or ""
                messages.append({"role": "assistant", "content": content})
                record = StepRecord(kind="final", content=content)
                steps.append(record)
                self.on_step(record)
                return AgentResult(final_message=content, steps=steps, hit_step_limit=False)

            # Record the assistant's tool-call turn in history.
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )

            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments", {})

                result = self._call_tool(name, arguments)

                record = StepRecord(
                    kind="tool_call",
                    tool_name=name,
                    tool_args=arguments if isinstance(arguments, dict) else {"raw": arguments},
                    tool_result=result,
                )
                steps.append(record)
                self.on_step(record)

                messages.append(
                    {
                        "role": "tool",
                        "name": name,
                        "content": json.dumps(result, default=str),
                    }
                )

        # Step budget exhausted - ask the model for a final summary rather
        # than just cutting it off silently.
        messages.append(
            {
                "role": "user",
                "content": (
                    "You have reached the maximum number of steps allowed for this task. "
                    "Summarize the current state honestly: what you tried, what worked, "
                    "what did not, and what you would do next. Do not claim success unless "
                    "tests actually passed."
                ),
            }
        )
        final = self.llm.chat(messages, tools=[])
        content = final.get("content") or "(no final summary produced)"
        record = StepRecord(kind="final", content=content)
        steps.append(record)
        self.on_step(record)
        return AgentResult(final_message=content, steps=steps, hit_step_limit=True)
