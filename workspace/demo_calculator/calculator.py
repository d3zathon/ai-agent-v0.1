"""A tiny calculator module - used as a demo target for the agent.

NOTE: This file intentionally ships with a bug in `divide()` (it uses `+`
instead of `/`). It exists so you can point the agent at this workspace
and watch it: read the code, run the tests, see the failure, fix the bug,
and re-run the tests until they pass. See README.md for the walkthrough.
"""


def add(a: float, b: float) -> float:
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def multiply(a: float, b: float) -> float:
    return a * b


def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a + b  # BUG: should be `a / b`
