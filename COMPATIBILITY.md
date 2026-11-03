# Client compatibility

How the Shipyard plugin is distributed, installed, authenticated, permissioned, supported and validated in each
supported client. Update this file whenever a client is re-tested, a listing changes state, or a CLI or plugin release
changes any row. Each section says when it was last checked.

Full setup and permission recipes are in the [Shipyard MCP guide](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md).
This file summarizes them and records what has been verified.

## Summary

| | Claude Code | Codex | Cursor | Kiro |
|---|---|---|---|---|
| Install from GitHub | ✅ | ✅ | ✅ | ✅ |
| Validated on CLI 1.10.0 | ✅ 2026-10-29 | ✅ 2026-10-29 | ✅ 2026-11-03 | ✅ 2026-11-03 |
| Uses read-only/destructive tool hints | ✅ | ✅ (`writes` mode) | ❌ app uses Auto-Run | ❌ |
| Marketplace listing | Not submitted | Blocked | Not submitted | Not submitted |

## Distribution and install

Every client installs the same plugin from this repository's `main` branch. The plugin contains no code: it starts
`shipyard mcp serve`, so the [Shipyard CLI](https://github.com/shipyard/shipyard-cli#installation) must be on the
`PATH` the client sees.

| Client | Mechanism | Install |
|---|---|---|
| Claude Code | Self-hosted plugin marketplace | `claude plugin marketplace add shipyard/shipyard-plugins`, then `claude plugin install shipyard@shipyard` |
| Codex | Self-hosted plugin marketplace | `codex plugin marketplace add shipyard/shipyard-plugins`, then `codex plugin add shipyard@shipyard` |
| Cursor | Plugin marketplace added by URL (Cursor app only) | `cursor-agent plugin marketplace add https://github.com/shipyard/shipyard-plugins`, then Cursor Settings, Plugins: install Shipyard |
| Kiro | Power imported from GitHub (IDE), Agent Plugins format; no `POWER.md` needed | Powers panel: install from GitHub URL `https://github.com/shipyard/shipyard-plugins` |

Claude Desktop and other MCP clients aren't plugin targets. Add `shipyard mcp serve` to them by hand as in the
[MCP guide](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#connecting-a-client).

## Versions

| | Requirement |
|---|---|
| Plugin | `v0.1.0`. Versioned with semver in `plugin.json` and `.claude-plugin/plugin.json`; see [RELEASING.md](RELEASING.md) |
| Shipyard CLI | 1.9.0 or later; **1.10.0 or later recommended** (adds tool hints, `shipyard upgrade`, stale-CLI notice) |
| MCP protocol | `2025-06-18`, `2025-03-26`, `2024-11-05` |

Client versions tested:

| Client | Versions |
|---|---|
| Claude Code | 2.1.284 (hand-added server); 2.1.312 (plugin) |
| Codex CLI | 0.162.0 (hand-added server and plugin) |
| Cursor | 3.24.9 and `cursor-agent` 2026.10.01 (hand-added server); 3.27.22 (plugin) |
| Kiro | IDE 1.2.56 and `kiro-cli` 2.28.0 (hand-added server); IDE 1.3.0 (plugin) |

Newer client versions should work.

## Authentication

The same in every client. The plugin never asks for or stores a token, and CI on this repository rejects any token,
`env`, `envFile` or `headers` key in the manifests.

- **Interactive use:** `shipyard login` once. The CLI keeps the token in `~/.shipyard/config.yaml` and the server reads
  it from there. This token reaches every org the user belongs to.
- **Shared machines, CI, or one org only:** an org API key, set as `SHIPYARD_API_TOKEN` in the client's per-user MCP
  config. That needs a hand-added server, not the plugin.

Known issue: `shipyard login` passes the token in a URL, so it can land in browser history
([SHIP-463](https://linear.app/stackgen/issue/SHIP-463)).

## Permissions

The CLI (1.10.0+) marks 14 tools read-only, 15 destructive and 4 as adding state only. `exec_service` is off unless
enabled in `~/.shipyard/config.yaml`. How each client uses that:

| Client | Plugin server name | Asks before destructive tools? | Read-only recipe for unattended use |
|---|---|---|---|
| Claude Code | `plugin:shipyard:shipyard` (tools `mcp__plugin_shipyard_shipyard__*`) | Yes, unless allowed; non-interactive sessions refuse | Allow `mcp__plugin_shipyard_shipyard__get_*` |
| Codex | `shipyard` | Yes; withheld from sessions that can't ask, with `default_tools_approval_mode = "writes"` | Full `[mcp_servers.shipyard]` block with `writes` mode (replaces the plugin's server) |
| Cursor | `plugin-shipyard-shipyard` | **Only with Auto-Run set to Ask Every Time or MCP Tools Protection on.** Otherwise it runs them without asking | Cursor app: Ask Every Time. `cursor-agent` doesn't load the plugin; use a hand-added server with `Mcp(shipyard:get_*)` |
| Kiro | `power-shipyard-plugins-shipyard` | IDE not verified; `kiro-cli --no-interactive` runs every tool without asking, ignoring hints | Uninstall the power, use a hand-added server with `disabledTools` |

Details and exact config: [Permissions and least privilege](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#permissions-and-least-privilege).

## Updates, uninstall and rollback

| Client | Update | Uninstall |
|---|---|---|
| Claude Code | `claude plugin update shipyard@shipyard` | `claude plugin uninstall shipyard@shipyard` |
| Codex | `codex plugin marketplace upgrade shipyard` | `codex plugin remove shipyard@shipyard` |
| Cursor | `cursor-agent plugin marketplace update shipyard`, then reinstall from Settings, Plugins | Uninstall from Settings, Plugins, then `cursor-agent plugin marketplace remove shipyard` |
| Kiro | Uninstall the power and import it again (Kiro's update check fails with "No name was provided for author") | Powers panel: uninstall |

Update the CLI with `shipyard upgrade` (1.10.0+) or `brew upgrade shipyard`. Roll back the CLI by pointing the client
at an earlier release's binary, checked against its `checksums.txt`
([Rolling back](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#rolling-back)). Roll back the plugin
with a forward version bump ([RELEASING.md](RELEASING.md)).

## Support

The same for every client:

- Install and setup problems: [support@shipyard.build](mailto:support@shipyard.build) or an issue on this repository.
- Wrong tool behaviour: an issue on [shipyard/shipyard-cli](https://github.com/shipyard/shipyard-cli/issues).
- Security problems: [security@shipyard.build](mailto:security@shipyard.build), never a public issue.

## Validation

Each run used a clean profile, installed the plugin from GitHub, ran a read-only workflow (list orgs, list
environments) and checked that a destructive tool was refused or asked first, then uninstalled. Destructive checks use
a nonexistent environment ID only.

| Client | Last validated | CLI | Result |
|---|---|---|---|
| Claude Code | 2026-10-29 | 1.10.0 | Reads run with `mcp__plugin_shipyard_shipyard__get_*` allowed; `stop_environment` refused |
| Codex | 2026-10-29 | 1.10.0 | Skill loads; `get_orgs` runs in `writes` mode; `stop_environment` refused ("requires approval") |
| Cursor | 2026-11-03 | 1.10.0 | Install, reads and skill work; with Ask Every Time, `stop_environment` asks first |
| Kiro | 2026-11-03 | 1.10.0 | GitHub import, reads and uninstall work |

Known client issues:

- Cursor ignores `destructiveHint`; with Auto-Run on and MCP Tools Protection off it ran `stop_environment` without
  asking (3.27.22).
- Kiro CLI writes `~/.kiro/powers/installed.json` without a version, after which the IDE refuses power installs
  ([kirodotdev/Kiro#11924](https://github.com/kirodotdev/Kiro/issues/11924)).
- Claude Code skips the plugin's server if a hand-added server runs the same command (2.1.312).

## Marketplace listings

| Client | Marketplace | State | Blocker |
|---|---|---|---|
| Claude Code | Claude plugin directory | Not submitted | Icon, privacy policy and ToS update, listing owner |
| Codex | OpenAI plugin directory | Blocked | Accepts only hosted HTTPS+OAuth servers: needs the hosted server live (SHIP-558), plus OpenAI business and domain verification |
| Cursor | Cursor Marketplace | Not submitted | Icon, privacy policy and ToS update, listing owner |
| Kiro | Kiro Powers | Not submitted | Icon, privacy policy and ToS update, listing owner, Publisher Terms accepted |

Until a listing is published, install from GitHub as above.
