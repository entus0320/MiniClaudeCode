# MiniClaudeCode

MiniClaudeCode is a lightweight Python terminal AI coding assistant inspired by Claude Code.

This repository is in Chapter 09: centralized command system. It supports streaming multi-turn chat plus bounded local and MCP tool use, finite ReAct tool loops, provider-neutral prompt modules, runtime context injection, Anthropic prompt caching markers, project-level policy rules, session-level temporary approvals, MCP server tool discovery, OpenAI-compatible fallback rendering, tool-result artifact offloading, automatic context compaction, project/user instruction file loading with safe `@include`, append-only JSONL session persistence with meta-indexed recovery, automatic memory management with LLM-driven note updates, and registry-driven slash commands with help and completion metadata.

## Current Status

Implemented:

- Python `src/` package layout in [src/miniclaudecode/](src/miniclaudecode/)
- YAML configuration loading from [config.example.yaml](config.example.yaml)
- credential loading through `.env` variable names from [.env.example](.env.example)
- provider-neutral in-memory conversation history
- Provider abstraction for pure text streaming and tool-aware turns
- Anthropic Claude provider using the official Anthropic Python SDK
- OpenAI / OpenAI-compatible provider using the official OpenAI Python SDK
- Provider factory based on `active_provider` and `protocol`
- Prompt assembly system with stable global modules, runtime context blocks, and dynamic system reminders
- Anthropic prompt caching markers for stable tool definitions and stable prompt blocks plus provider-normalized cache usage parsing
- OpenAI-compatible prompt fallback that renders assembled prompts as a standard system message without Claude-style cache fields
- Textual TUI with message history, input area, provider/model status, mode-aware status hints, streaming output, registry-driven slash commands, and best-effort interruption
- Chapter 02 tool system with six local tools:
  - `read_file`
  - `write_file`
  - `edit_file`
  - `run_command`
  - `glob_files`
  - `grep_content`
- Finite ReAct Agent Loop with a default 20-round tool-enabled limit plus one no-tool final synthesis when the limit is reached
- Multiple sequential tool rounds for workflows such as search → read → summarize
- Agent event stream for model text deltas, tool calls, tool results, final replies, errors, cancellation, and max-round termination
- Agent-level plan-only option that allows read tools and blocks write/edit/command tools before execution; this is a foundation, not a full TUI/CLI productized approval mode yet
- Policy engine for tool execution decisions: `allow`, `ask`, and `deny`
- Project-level policy loading from optional `miniclaudecode.policy.yaml`
- Session-level temporary approvals through `/allow-session`
- MCP JSON-RPC tool clients with `stdio` and `streamable_http` transports
- MCP server lifecycle support for `initialize`, `tools/list`, and `tools/call`
- MCP tool registration using `server_name__tool_name` names and the existing `Tool` interface
- Connection reuse plus bounded reconnect/retry behavior for MCP servers
- Planned concurrency-safe Agent Loop execution for local read tools and MCP `safe_tools`
- Confirmation gate for tools that need approval: `write_file`, `edit_file`, `run_command`, and MCP tools
- Workspace path protection for local file tools
- Dangerous command detection for destructive shell commands, remote script execution, force push, and publishing commands
- CLI entry point: `miniclaudecode`
- fake/mock tests for session, providers, tools, CLI, TUI, and streaming/tool-flow behavior
- Context management with tool-result artifact offloading and automatic structured summarization
- `/compact` manual compaction command with safe message-group boundaries
- Project and user instruction file loading with priority-based ordering
- Safe `@include` directive for modular instruction files with depth limits and path sandboxing
- Append-only JSONL session transcript storage with per-session meta index
- Session recovery with bad-line skipping, incomplete-tool-group truncation, and time-gap notices
- Automatic memory management: user-level and project-level notes updated by LLM every N turns, on `/notes-save`, and on exit
- TUI commands for session inspection (`/session`, `/sessions`, `/resume`) and memory control (`/notes`, `/notes-path`, `/notes-save`)
- Centralized slash command registry with command metadata, aliases, conflict detection, `/help`, unknown-command guidance, and Tab completion

Still not implemented:

- unlimited autonomous Agent loop
- complex autonomous planning beyond the bounded ReAct tool loop
- parallel execution for side-effecting tools
- complete MCP protocol coverage beyond MCP tool discovery and calls
- complex patch/diff application
- project indexing, semantic retrieval, or RAG
- user-global permission policy files
- permanent `/allow-always` policy writes
- OS-level command sandbox isolation
- cross-project workspace access
- automatic git commit, git push, or publishing

## Requirements

- Python 3.11+

## Install for Local Development

```bash
python -m pip install -e .
```

In this workspace, the local virtual environment is [minicode/](minicode/), so commands can also be run with:

```bash
./minicode/Scripts/python -m pip install -e .
```

## Configuration

Copy the example files:

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

Fill only the environment variable value for the provider you want to use:

```dotenv
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
```

Do not put real API keys in source files, YAML examples, logs, tests, or commits.

### Project policy

MiniClaudeCode optionally reads a project-level `miniclaudecode.policy.yaml` from the startup working directory. See [miniclaudecode.policy.example.yaml](miniclaudecode.policy.example.yaml) for the supported shape.

Policy decisions are:

- `allow`: execute the tool call
- `ask`: pause for user approval
- `deny`: reject before execution

Supported modes are:

- `strict`: safe reads are allowed after sandbox checks; writes, edits, and commands ask by default
- `default`: safe reads are allowed; writes, edits, and commands ask by default
- `permissive`: ordinary writes, edits, and commands can run without asking, but hard safety boundaries still deny

Hard boundaries cannot be bypassed by permissive mode, project rules, or session approvals: workspace escape, sensitive files, project policy file writes, and clearly dangerous commands are denied before execution.

Chapter 05 only reads project policy files. It does not implement user-global policy files, `/allow-always`, or permanent policy writes.

### MCP servers

MiniClaudeCode can optionally read `mcp_servers` from `config.yaml` and discover MCP tools over JSON-RPC 2.0.

Supported transports:

- `stdio`: starts a local subprocess and exchanges line-delimited JSON-RPC messages through stdin/stdout
- `streamable_http`: sends JSON-RPC requests to an HTTP endpoint and accepts JSON or simple `data: {...}` stream responses

Supported lifecycle methods are `initialize`, `tools/list`, and `tools/call`. Discovered tools are registered with a server prefix, for example `fake__echo`, and are exposed through the same `Tool` interface as local tools.

MCP tools are conservative by default: they require confirmation and still go through the existing `PolicyEngine`. The `safe_tools` config only controls whether a disconnected in-flight `tools/call` may be retried after reconnect and whether that MCP tool is eligible for concurrency-safe batch execution; it does not grant permission to execute the tool.

MCP server config may include `install_command` and `documentation_url`. These are shown as user guidance only. MiniClaudeCode does not automatically run install commands, download tools, or modify the environment.

Connection recovery is bounded. MiniClaudeCode can restart a broken stdio server or reconnect an HTTP server, then rerun `initialize` and `tools/list`. Unsafe in-flight tool calls are not replayed after a disconnect, to avoid duplicate side effects.

Chapter 06 does not implement the full MCP protocol, OAuth, automatic third-party server installation, server-to-client tool calls, side-effecting tool parallelism, or cross-session persistent connections.

### Anthropic

Use `active_provider: anthropic` in `config.yaml`. The default example model is `claude-opus-4-8`.

Claude thinking is configured under the Anthropic provider. It is disabled by default. When enabled, MiniClaudeCode requests adaptive thinking and still only displays final text deltas in the TUI.

### OpenAI-compatible

Use `active_provider: openai-compatible`, then set:

```yaml
base_url: https://your-compatible-endpoint.example/v1
model: your-compatible-model
```

Pure chat requires OpenAI Chat Completions streaming compatibility. Tool calling additionally requires the compatible service to support Chat Completions tool calling and streaming `tool_calls` deltas.

## Tool System

MiniClaudeCode exposes six built-in tools to the active model:

| Tool | Purpose | Confirmation |
| --- | --- | --- |
| `read_file` | Read a UTF-8 text file inside the project workspace | No |
| `glob_files` | Find files by a glob pattern inside the project workspace | No |
| `grep_content` | Search UTF-8 text files with a regular expression | No |
| `write_file` | Write a UTF-8 file inside the project workspace | Yes |
| `edit_file` | Replace one unique old text string in a file | Yes |
| `run_command` | Run a shell command in the project workspace | Yes |

Tool safety boundaries:

- file paths are restricted to the startup working directory
- `.env`, `.env.*`, `config.yaml`, private key files such as `*.pem` and `*.key`, and ignored local environment directories are treated as sensitive local paths
- `miniclaudecode.policy.yaml` may be read as project configuration but cannot be written or edited through tools
- `write_file`, `edit_file`, and `run_command` ask for confirmation in the default permission mode
- command execution has a timeout and captures stdout, stderr, and exit code
- clearly dangerous commands such as destructive deletion, remote script download-and-execute, `git push --force`, and `npm publish` are denied before execution
- the finite Agent Loop allows multiple sequential tool rounds and stops executing tools after 20 tool-enabled ReAct rounds by default
- when the tool round limit is reached, MiniClaudeCode performs one no-tool final synthesis: it answers from existing information when possible, or states that current information is insufficient
- the Agent-level plan-only option adds a dynamic system reminder, allows read tools, and blocks write/edit/command tools before execution, but it does not yet expose a full TUI/CLI approval workflow
- stable prompt modules and tool descriptions reinforce key rules such as preferring dedicated file/search tools over shell commands and reading files before editing

Inside the TUI, when a tool asks for confirmation, choose from the menu:

- `1. Yes` to execute it once
- `2. Yes, and don't ask again for this pattern` to allow the same kind of call for the current process session
- `3. No` to reject it
- type `/interrupt` or press Ctrl+C for best-effort interruption

Legacy `/allow`, `/allow-session`, and `/deny` commands are still accepted for compatibility. Session approvals are in-memory only. They are not written to `miniclaudecode.policy.yaml` and cannot override hard denies.

## Run

After installing in editable mode:

```bash
miniclaudecode
```

Or without relying on the console script:

```bash
PYTHONPATH=src python -m miniclaudecode.cli
```

Inside the TUI:

- type a message and press Enter to chat
- type `/help` to list local commands
- type `/exit` to exit with code 0
- type `/interrupt` or press Ctrl+C during generation for best-effort interruption
- type a command prefix such as `/comp` and press Tab for command completion
- choose `1. Yes`, `2. Yes, and don't ask again for this pattern`, or `3. No` when a tool confirmation is pending

Conversation history is stored in append-only JSONL session transcripts when session persistence is enabled.

## Verify

Compile source files:

```bash
PYTHONPATH=src ./minicode/Scripts/python -m compileall src/miniclaudecode
```

Run the unit and fake e2e tests with the standard library test runner:

```bash
PYTHONPATH=src ./minicode/Scripts/python -m unittest discover -s tests
```

Check importability:

```bash
PYTHONPATH=src ./minicode/Scripts/python -c "from miniclaudecode.cli import main; print(callable(main))"
```

Safety checks:

```bash
grep -r "sk-ant\|sk-proj\|sk-" src tests docs config.example.yaml README.md
grep -r "api_key" src
```

The `api_key` check may show SDK parameter names such as `api_key=config.credential`; it must not show hardcoded secret values. The first check may also show the grep pattern itself in documentation; it must not show real secret values.

## Manual Real API Acceptance

These checks require local credentials and network access. They are not part of the default automated tests.

### Anthropic tool calling

1. Copy [config.example.yaml](config.example.yaml) to `config.yaml`.
2. Copy [.env.example](.env.example) to `.env`.
3. Set `active_provider: anthropic` in `config.yaml`.
4. Set `ANTHROPIC_API_KEY` in `.env`.
5. Run `miniclaudecode`.
6. Input `搜索 README.md 并总结项目当前状态`.
7. Confirm that one or more tool calls are shown and the final answer summarizes README content.
8. Input `/exit` and confirm normal exit.

### Anthropic prompt caching

1. Use the Anthropic setup above.
2. Run two similar requests whose stable prompt and tool list are unchanged.
3. Inspect the normalized provider usage in debug/test instrumentation or provider events.
4. Anthropic requests keep tool definitions in `tools`, stable prompt and runtime context in `system`, and conversation history in `messages`.
5. Cache breakpoints are placed on the last tool definition and the last stable system block; runtime context and system reminders remain after the stable breakpoint without cache markers.
6. On a large enough stable prefix, the first request may report `cache_creation_input_tokens > 0`; a later matching request may report `cache_read_input_tokens > 0`.
7. If both cache fields stay empty or zero, check that dynamic data such as date, working directory, Git status, and runtime reminders are not mixed into stable prompt blocks.

OpenAI-compatible providers ignore Claude-style cache markers. They receive the assembled prompt as a regular system message and do not promise prompt cache statistics.

### OpenAI-compatible tool calling

1. Set `active_provider: openai-compatible` in `config.yaml`.
2. Set the compatible provider `base_url` and `model`.
3. Set `OPENAI_API_KEY` in `.env`.
4. Confirm the compatible backend supports Chat Completions tool calling.
5. Run `miniclaudecode`.
6. Input `找到 MiniClaudeCodeApp 定义并读取相关文件`.
7. Confirm that one or more tool calls are shown and the final answer references the matching file path.
8. Input `/exit` and confirm normal exit.

## Chapter Documents

See:

- [docs/ch01/spec.md](docs/ch01/spec.md)
- [docs/ch01/tasks.md](docs/ch01/tasks.md)
- [docs/ch01/checklist.md](docs/ch01/checklist.md)
- [docs/ch02/spec.md](docs/ch02/spec.md)
- [docs/ch02/tasks.md](docs/ch02/tasks.md)
- [docs/ch02/checklist.md](docs/ch02/checklist.md)
- [docs/ch03/spec.md](docs/ch03/spec.md)
- [docs/ch03/tasks.md](docs/ch03/tasks.md)
- [docs/ch03/checklist.md](docs/ch03/checklist.md)
- [docs/ch04/spec.md](docs/ch04/spec.md)
- [docs/ch04/tasks.md](docs/ch04/tasks.md)
- [docs/ch04/checklist.md](docs/ch04/checklist.md)
- [docs/ch05/spec.md](docs/ch05/spec.md)
- [docs/ch05/tasks.md](docs/ch05/tasks.md)
- [docs/ch05/checklist.md](docs/ch05/checklist.md)
- [docs/ch06/spec.md](docs/ch06/spec.md)
- [docs/ch06/tasks.md](docs/ch06/tasks.md)
- [docs/ch06/checklist.md](docs/ch06/checklist.md)
- [docs/ch07/spec.md](docs/ch07/spec.md)
- [docs/ch07/tasks.md](docs/ch07/tasks.md)
- [docs/ch07/checklist.md](docs/ch07/checklist.md)
- [docs/ch08/spec.md](docs/ch08/spec.md)
- [docs/ch08/tasks.md](docs/ch08/tasks.md)
- [docs/ch08/checklist.md](docs/ch08/checklist.md)
- [docs/ch09/spec.md](docs/ch09/spec.md)
- [docs/ch09/tasks.md](docs/ch09/tasks.md)
- [docs/ch09/checklist.md](docs/ch09/checklist.md)
