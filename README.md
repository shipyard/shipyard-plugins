# Shipyard plugin for coding agents

Connect your coding agent to [Shipyard](https://shipyard.build) ephemeral environments. With this plugin, Claude Code,
Codex, Cursor or Kiro can put a repo on Shipyard, list your environments, read build history and service logs, rebuild
or restart environments, manage volume snapshots, and verify a pushed change against its preview environment before
you merge.

The plugin runs the Shipyard MCP server that ships with the Shipyard CLI (`shipyard mcp serve`) and adds a short skill
that tells the agent how to use it safely.

## Requirements

1. **Shipyard CLI 1.11.0 or later**, on the `PATH` your agent sees. 1.11.0 adds the tools that create an app from a
   repo. Older versions from 1.9.0 can still read and manage existing environments.

   ```bash
   brew tap shipyard/tap && brew install shipyard
   shipyard --version
   ```

   Other install methods: [Shipyard CLI](https://github.com/shipyard/shipyard-cli#installation).

2. **Log in once**, so the server can find your token:

   ```bash
   shipyard login
   ```

   The plugin never asks for or stores your token. The CLI keeps it in `~/.shipyard/config.yaml`. If you log in while
   your agent is open, restart the Shipyard MCP server in your client afterwards: it reads the token when it starts.

You don't have to do these steps first. Ask your agent to "put this repo on Shipyard" and it walks you through them,
as below.

## Getting started from your agent

Open your repo and ask your agent to "put this repo on Shipyard". The skill tells it to:

1. Check that the Shipyard CLI is installed and current, and offer to install or upgrade it.
2. Send you to [shipyard.build/signup](https://shipyard.build/signup) if you don't have an account, then run
   `shipyard login`, which opens your browser. You never paste a token into the chat.
3. Send you a link to install the Shipyard GitHub app when your org needs it.
4. Look for a Docker Compose file. If there isn't one, it analyzes the repo, writes one with Shipyard's routing labels,
   checks it with `docker compose config` (and `docker compose up` if Docker is running), and shows it to you. It
   never overwrites a compose file you already have.
5. Show you the plan (app name, services, routes and the environment variables it needs), create the app after you
   confirm, and watch the first build until the environment is ready.

It asks before it installs anything, commits, pushes or creates the app.

## Install

| Client | Install |
|---|---|
| Claude Code | `claude plugin marketplace add shipyard/shipyard-plugins`, then `claude plugin install shipyard@shipyard` |
| Codex | `codex plugin marketplace add shipyard/shipyard-plugins`, then `codex plugin add shipyard@shipyard` |
| Kiro | Powers panel: install from GitHub URL `https://github.com/shipyard/shipyard-plugins` |
| Cursor | `cursor-agent plugin marketplace add https://github.com/shipyard/shipyard-plugins`, then Cursor Settings, Plugins: install Shipyard |

Restart the session afterwards and ask "list my Shipyard environments".

- **Cursor: set Auto-Run first.** With Auto-Run on and MCP Tools Protection off, Cursor runs every Shipyard tool
  without asking, including ones that stop or rebuild environments. In Cursor Settings, Auto-Run, choose **Ask Every
  Time** or turn on **MCP Tools Protection**. The plugin's server runs in the Cursor app only: `cursor-agent` doesn't
  load it.
- **Use the plugin or a hand-added server, not both.** If you added `shipyard mcp serve` to your client yourself,
  remove it first (for example `claude mcp remove shipyard`). Codex is the exception: a `[mcp_servers.shipyard]` entry
  replaces the plugin's server.
- **If the server doesn't start** with "command not found", your client can't see `shipyard` on its `PATH` (common for
  desktop apps opened from the Dock). The plugin can't change that command, so add the server by hand with the
  absolute path from `which shipyard`, as in the [MCP guide](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#connecting-a-client).

## What it can do, and what it sends

The MCP server calls the Shipyard API (`https://shipyard.build`) with your token. It reads and changes only the
environments, services, volumes, snapshots and environment variables your token can access. It sends nothing anywhere
else. The CLI plugin itself contains no code; it only tells your agent how to start `shipyard mcp serve`.

- Read-only tools list environments, builds, services, logs, volumes, snapshots and environment variables (hidden
  values masked).
- Other tools create and reconfigure apps, stop, rebuild, restart, update branches, set or delete environment
  variables, reset or restore volumes, and create environments. Claude Code and Codex ask before using them unless you allow them. Cursor asks only if
  Auto-Run is set as above. Kiro's CLI runs tools without asking; see the per-client settings below.
- `exec_service` (run a command in a container) is **off** unless you enable it.

For unattended agents, allow only the read-only tools. The plugin's tools have different names from a hand-added
server's (in Claude Code, `mcp__plugin_shipyard_shipyard__*`). Per-client settings are in
[Permissions and least privilege](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#permissions-and-least-privilege).

## Hosted plugin: nothing to install

`shipyard-hosted` connects your agent to Shipyard's hosted MCP server (`https://mcp.shipyard.build/mcp`) instead of
running the CLI. After installing, connect it from your client: `/mcp` in Claude Code, `codex mcp login shipyard` in
Codex. Your browser opens Shipyard: sign in, pick the organization the agent works in, and allow access. Your client
keeps the token; the plugin holds none.

It adds an app to Shipyard from a pushed GitHub branch, reconfigures it, and checks its environments, builds and
failures. A reconfiguration can rebuild the app, restart a stopped one and unlink repos, and your agent should ask
first: the plugin's skill tells it to, but your client decides whether it can run without asking. There's no tool to
stop or restart an environment, rebuild on request, set env vars, read service logs or run commands; use the CLI
plugin above, or the Shipyard dashboard, for those.

| Client | Install |
|---|---|
| Claude Code | `claude plugin marketplace add shipyard/shipyard-plugins`, then `claude plugin install shipyard-hosted@shipyard` |
| Codex | `codex plugin marketplace add shipyard/shipyard-plugins`, then `codex plugin add shipyard-hosted@shipyard` |

This plugin is new: signing in through it, in any client, and using it in Cursor or Kiro haven't been tested yet.

Install one of the two plugins, not both: each defines a server named `shipyard`.

To disconnect, remove the server or sign out of it in your client's MCP settings. To update the plugin, use
`claude plugin update shipyard-hosted@shipyard` or `codex plugin marketplace upgrade shipyard`. To remove it, use
`claude plugin uninstall shipyard-hosted@shipyard` or `codex plugin remove shipyard-hosted@shipyard`.

## Update and uninstall

- Update the CLI: `brew upgrade shipyard`, or `shipyard upgrade` on CLI 1.10.0 or later.
- Update the plugin: `claude plugin update shipyard@shipyard`, or `codex plugin marketplace upgrade shipyard`. In
  Cursor, run `cursor-agent plugin marketplace update shipyard`, then reinstall from Cursor Settings, Plugins. In Kiro,
  uninstall the power and import it from GitHub again: Kiro's update check can fail with "No name was provided for
  author".
- Remove it: `claude plugin uninstall shipyard@shipyard`, `codex plugin remove shipyard@shipyard`, or remove it from the
  Kiro Powers panel or Cursor's plugin settings (then `cursor-agent plugin marketplace remove shipyard`).

## Troubleshooting

See [Troubleshooting](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#troubleshooting) in the MCP
guide. That includes Kiro refusing to install a power with "Invalid installed.json schema" after the Kiro CLI has
touched `~/.kiro/powers/installed.json`.

## Support, privacy and security

- Docs: [Shipyard MCP guide](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md)
- Supported clients, versions and validation status: [COMPATIBILITY.md](COMPATIBILITY.md)
- Support: [support@shipyard.build](mailto:support@shipyard.build)
- Security reports: [security@shipyard.build](mailto:security@shipyard.build), see
  [shipyard.build/security](https://shipyard.build/security/)
- [Privacy policy](https://shipyard.build/privacy/) and [terms](https://shipyard.build/terms/)

## License

Apache-2.0. See [LICENSE](LICENSE).
