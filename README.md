# Shipyard plugin for coding agents

Connect your coding agent to [Shipyard](https://shipyard.build) ephemeral environments. With this plugin, Claude Code,
Codex, Cursor or Kiro can list your environments, read build history and service logs, rebuild or restart
environments, manage volume snapshots, and verify a pushed change against its preview environment before you merge.

The plugin runs the Shipyard MCP server that ships with the Shipyard CLI (`shipyard mcp serve`) and adds a short skill
that tells the agent how to use it safely.

## Requirements

1. **Shipyard CLI 1.9.0 or later**, on the `PATH` your agent sees. 1.10.0 or later is recommended: it labels each
   tool as read-only or destructive, which some clients use to decide when to ask you.

   ```bash
   brew tap shipyard/tap && brew install shipyard
   shipyard --version
   ```

   Other install methods: [Shipyard CLI](https://github.com/shipyard/shipyard-cli#installation).

2. **Log in once**, so the server can find your token:

   ```bash
   shipyard login
   ```

   The plugin never asks for or stores your token. The CLI keeps it in `~/.shipyard/config.yaml`.

## Install

| Client | Install |
|---|---|
| Claude Code | `claude plugin marketplace add shipyard/shipyard-plugins`, then `claude plugin install shipyard@shipyard` |
| Codex | `codex plugin marketplace add shipyard/shipyard-plugins`, then `codex plugin add shipyard@shipyard` |
| Kiro | Powers panel: install from GitHub URL `https://github.com/shipyard/shipyard-plugins` |
| Cursor | Settings, Plugins: add from this repository |

Restart the session afterwards and ask "list my Shipyard environments".

## What it can do, and what it sends

The MCP server calls the Shipyard API (`https://shipyard.build`) with your token. It reads and changes only the
environments, services, volumes, snapshots and environment variables your token can access. It sends nothing anywhere
else. The plugin itself contains no code; it only tells your agent how to start `shipyard mcp serve`.

- Read-only tools list environments, builds, services, logs, volumes, snapshots and environment variables (hidden
  values masked).
- Other tools stop, rebuild, restart, update branches, set or delete environment variables, reset or restore volumes,
  and create environments. Your agent asks before using them unless you allow them.
- `exec_service` (run a command in a container) is **off** unless you enable it.

For unattended agents, allow only the read-only tools. Per-client settings are in
[Permissions and least privilege](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#permissions-and-least-privilege).

## Update and uninstall

- Update the CLI: `shipyard upgrade` (1.10.0 or later) or `brew upgrade shipyard`.
- Update the plugin: `claude plugin update shipyard@shipyard`, or `codex plugin marketplace upgrade`.
- Remove it: `claude plugin uninstall shipyard@shipyard`, `codex plugin remove shipyard@shipyard`, or remove it from the
  Kiro Powers panel or Cursor's plugin settings.

## Troubleshooting

See [Troubleshooting](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#troubleshooting) in the MCP
guide. That includes Kiro refusing to install a power with "Invalid installed.json schema" after the Kiro CLI has
touched `~/.kiro/powers/installed.json`.

## Support, privacy and security

- Docs: [Shipyard MCP guide](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md)
- Support: [support@shipyard.build](mailto:support@shipyard.build)
- Security reports: [security@shipyard.build](mailto:security@shipyard.build), see
  [shipyard.build/security](https://shipyard.build/security/)
- [Privacy policy](https://shipyard.build/privacy/) and [terms](https://shipyard.build/terms/)

## License

Apache-2.0. See [LICENSE](LICENSE).
