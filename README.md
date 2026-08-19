# security-agent (v0.1)

A local, Windows-first coding agent that uses your **own locally running
Ollama model** to create, edit, test, debug, and explain software —
including authorized cybersecurity tooling for targets you own or have
explicit permission to test.

It runs entirely on your machine, talks to Ollama over `http://127.0.0.1:11434`,
and is restricted to a sandboxed `workspace/` folder. No cloud calls, no
telemetry, no multi-agent orchestration — a clean single-agent loop:

```
User → LLM → Tool → Result → LLM → ... → Final answer
```

---

## 1. Requirements

- Windows 10/11 (also runs on macOS/Linux for development, since it's plain Python)
- Python 3.11+
- [Ollama](https://ollama.com) installed and running locally
- A tool-calling-capable model pulled into Ollama, e.g.:
  ```
  ollama pull qwen2.5-coder:7b
  ```
  (Any Ollama model that supports function/tool calling will work —
  `qwen2.5-coder`, `llama3.1`, `mistral-nemo`, etc. Model choice affects
  quality of tool-use; not every model handles tool calling well.)

## 2. Setup (Windows)

```powershell
# from the security-agent/ folder
.\run_agent.ps1
```

This will:
1. Create a `.venv` virtual environment if one doesn't exist.
2. Install `requirements.txt` (`ollama`, `pytest`).
3. Launch the agent's interactive prompt.

If PowerShell blocks the script the first time, allow local scripts once
(per-user, no admin required):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Setup (macOS/Linux, for development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m agent.main
```

## 3. Configuration

Edit `config/config.json`:

```json
{
  "ollama": {
    "host": "http://127.0.0.1:11434",
    "model": "REPLACE_WITH_YOUR_MODEL"
  },
  "workspace": "./workspace",
  "agent": {
    "max_steps": 12
  },
  "terminal": {
    "timeout_seconds": 30,
    "allowlist": ["python", "python3", "pytest", "git", "pip"]
  }
}
```

- `ollama.model` — set this to a model you've pulled (e.g. `"qwen2.5-coder:7b"`).
  The agent prints a warning at startup if you leave the placeholder in place.
- `workspace` — the sandbox directory. All file tools are hard-restricted to
  this folder (resolved relative to the project root, not your shell's cwd).
- `agent.max_steps` — hard cap on tool-calling iterations per task
  (default `12`), so a confused model can't loop forever.
- `terminal.allowlist` — which program names `run_command` may execute.

## 4. Usage

**Interactive REPL:**
```powershell
.\run_agent.ps1
```
```
you> Look at demo_calculator, run its tests, and fix any failures.
```

**Single task from the command line:**
```powershell
.\run_agent.ps1 "Look at demo_calculator, run its tests, and fix any failures."
```

Or directly with Python once the venv is set up:
```powershell
.venv\Scripts\python.exe -m agent.main "your task here"
```

The agent will stream each tool call and its result to the console as it
works, then print a final answer. Type `exit` or `quit` to leave the REPL.

## 5. Demo: watch it find and fix a real bug

`workspace/demo_calculator/` ships with an **intentional bug**:
`divide()` uses `+` instead of `/`. Its test suite
(`test_calculator.py`) currently has one failing test.

Point the agent at it:

```
you> Investigate demo_calculator, run its tests, diagnose any failures, fix them, and re-run the tests to confirm they pass.
```

Expected behavior:
1. Agent calls `list_files("demo_calculator")` / `read_file(...)` to inspect the code.
2. Agent calls `run_tests("demo_calculator")` → sees `test_divide` fail
   (`assert divide(10, 2) == 5` → got `12`).
3. Agent reads `calculator.py`, spots the `a + b` bug in `divide()`.
4. Agent calls `write_file("demo_calculator/calculator.py", ...)` with the fix (`a / b`).
5. Agent calls `run_tests("demo_calculator")` again → all 5 tests pass.
6. Agent reports success — **only because it verified it**, not because it assumed it.

This is the full create → run → detect failure → fix → re-verify loop
described in the project brief, using real tool calls end to end.

> Note: this demo bug is intentionally left in place so you can watch the
> agent fix it. It is separate from `tests/test_agent.py`, which tests the
> **agent framework itself** and passes as-is (see below).

## 6. Available tools

| Tool | Purpose |
|---|---|
| `list_files(path)` | List files/dirs under a workspace-relative path |
| `read_file(path)` | Read a text file's contents |
| `write_file(path, content)` | Create/overwrite a text file |
| `run_command(command, timeout_seconds)` | Run an allowlisted CLI command, no shell |
| `run_tests(path, timeout_seconds)` | Run `pytest` against a path, report pass/fail + output |

## 7. Security model

- **Filesystem sandbox** — every file tool resolves the given path against
  the workspace root and rejects anything that would escape it: absolute
  paths, drive letters (`C:\...`), UNC paths (`\\server\share`), and `..`
  traversal. Rejections return a normal error result to the LLM rather than
  crashing the agent.
- **Command allowlist** — `run_command` only executes programs named
  `python`, `python3`, `pytest`, `git`, or `pip` (configurable). It never
  invokes a shell (`shell=False`), so operators like `&&`, `|`, `;`,
  backticks, and redirection cannot chain in a second command — they're
  rejected outright before anything runs. PowerShell, `cmd.exe`, `bash`,
  and other general-purpose shells are never reachable through this tool.
- **Timeouts** — every subprocess call (`run_command`, `run_tests`) has a
  configurable timeout and is killed if it's exceeded.
- **No credential/system access** — the agent has no tool that can read
  outside `workspace/`, so SSH keys, browser profiles, credential stores,
  and system directories are unreachable by construction, not by convention.
- **Step budget** — `agent.max_steps` bounds how many tool-calling
  iterations a single task can take, so a confused or looping model can't
  run indefinitely.
- **Cybersecurity work** — the agent's system prompt instructs it to treat
  all security/offensive-tooling requests as scoped to local, lab-owned, or
  explicitly authorized targets, and to decline anything else. You are
  responsible for only pointing it at systems you own or are authorized to test.

### Known limitations (by design, for v0.1)

- Allowlisting is at the **program** level, not the argument level: an
  allowed interpreter like `python` can still do a lot (e.g. `python -c
  "..."`, or a `git` hook, or a malicious `pip` package's install script).
  This is an inherent trade-off of allowing a general-purpose interpreter
  at all. Don't run this agent against untrusted instructions or in a
  security boundary you don't otherwise trust.
- This is a single local sandbox directory, not a container or VM. Treat it
  as "restricted," not "adversarially safe."
- No persistent memory across runs, no multi-agent orchestration, no
  Docker — intentionally out of scope for v0.1 per the project brief.

## 8. Project structure

```
security-agent/
├── agent/
│   ├── __init__.py
│   ├── main.py       # CLI entrypoint / REPL
│   ├── llm.py         # Ollama client wrapper + tool JSON schemas
│   ├── loop.py         # User → LLM → Tool → Result loop
│   └── prompts.py      # System prompt
├── tools/
│   ├── __init__.py
│   ├── filesystem.py   # list_files / read_file / write_file (sandboxed)
│   ├── terminal.py     # run_command (allowlisted, no shell)
│   └── testing.py      # run_tests (pytest runner)
├── workspace/
│   └── demo_calculator/
│       ├── calculator.py       # ships with one intentional bug
│       └── test_calculator.py  # 1 failing test until fixed
├── config/
│   └── config.json
├── tests/
│   └── test_agent.py   # tests the agent framework itself (fake LLM, no network)
├── requirements.txt
├── run_agent.ps1
├── .gitignore
└── README.md
```

## 9. Running the project's own tests

These test the agent framework (sandboxing, allowlisting, the loop's
step-limit behavior) using a scripted fake LLM — they don't need Ollama
running:

```powershell
.venv\Scripts\python.exe -m pytest tests -v
```

They are independent of the demo calculator's intentionally-failing test.

## 10. Troubleshooting

- **"Could not reach Ollama..."** — make sure Ollama is running
  (`ollama serve`, or the desktop app) and that `ollama.host` in
  `config/config.json` matches (`http://127.0.0.1:11434` by default).
- **Model ignores tools / never calls a function** — not all Ollama models
  support tool calling well. Try `qwen2.5-coder`, `llama3.1`, or another
  model known to support the tool-calling API.
- **"not on the allowlist"** — the agent tried to run something outside
  `python/python3/pytest/git/pip`. This is expected behavior; if you need
  another tool, add it to `terminal.allowlist` deliberately and understand
  the risk before doing so.
