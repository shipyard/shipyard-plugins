---
name: shipyard
description: Use when the user asks about Shipyard environments (ephemeral, preview or pull request environments), their builds, services, logs, volumes or snapshots, or wants a pushed change verified against its environment.
---

# Shipyard

Use the `shipyard` MCP tools. They act on the user's real Shipyard environments.

- Read before you change anything: `get_environments` (filter by `repo_name` and `branch`), `get_environment`, `get_build_history`, `get_services`, `get_logs`.
- To verify a pushed change, use the server's `verify` prompt. It covers matching the pushed commit, waiting for `ready`, and reporting what was actually checked.
- Ask before tools that stop, rebuild, reset, overwrite or delete. Never rebuild while a build is in flight (`processing` is true).
- Never print, paste or commit a `bypass_token`.
- If a tool is missing, or the server fails with "unknown command \"mcp\"", the user's CLI is too old: suggest `shipyard upgrade` (or `brew upgrade shipyard`) and restarting the client.
- If calls fail with an auth error, ask the user to run `shipyard login`.
