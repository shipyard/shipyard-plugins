# Releasing the Shipyard plugin

How changes to this repository reach users, how to roll them back, what to watch, and where reports go. It contains
no credentials and no account details. Those stay with the account owners listed below.

## What gets released

The plugin has these parts, released separately:

| Part | Where it lives | Released by |
|---|---|---|
| The plugin: manifests, `mcp.json`/`.mcp.json`, `skills/` | This repository | A merge to `main`, then a version tag |
| The hosted plugin: `hosted/`, with its own manifests and skill | This repository | A merge to `main`, then a version tag |
| The MCP server (`shipyard mcp serve`) | [shipyard/shipyard-cli](https://github.com/shipyard/shipyard-cli) | A CLI release (tag `v*`), which also updates the Homebrew tap |
| The Kiro copy of the root plugin: `shipyard/` in Kiro's powers repo | [kirodotdev/powers](https://github.com/kirodotdev/powers) | A PR to that repo after each root plugin release, reviewed and merged by Kiro's team |
| The hosted MCP server (`https://mcp.shipyard.build/mcp`) | [shipyard/shipyard-cli](https://github.com/shipyard/shipyard-cli) | An image built from a CLI release tag and installed by hand; see [Deploying the hosted server](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#deploying-the-hosted-server) |

The root plugin contains no code and starts whatever `shipyard` binary is on the user's `PATH`, and the hosted plugin
only names the hosted server's URL. Most behaviour changes
(tools, annotations, instructions) therefore ship as **CLI releases**, not plugin releases. Release the plugin when
the manifests, server definition or skill change.

**`main` is live.** Self-hosted marketplace installs read this repository's default branch: `codex plugin marketplace
upgrade shipyard` picks up whatever is on `main`, and Claude Code reads the marketplace from it. Treat every merge to
`main` as a release. Branch protection requires a code-owner review and the `Manifests` and `Secret scan` checks.

So any PR that changes the manifests, `mcp.json`/`.mcp.json` or `skills/`, at the root or in `hosted/`, must bump that
plugin's version, even a one-line skill fix. Without a bump, Codex users get the change on their next update, but Claude Code users never do, because their
installed version still matches. CI doesn't check this yet; reviewers do. Changes to `README.md`, `RELEASING.md` or
`.github/` don't need a bump.

## Ownership

| Role | Owner |
|---|---|
| Plugin repository (merges, tags) | @daniellle, @akshaykalia (`CODEOWNERS`) |
| CLI releases and the Homebrew tap | @akshaykalia |
| Hosted MCP server deploys | @akshaykalia |
| Claude plugin directory listing | _unassigned_ |
| Cursor Marketplace listing | _unassigned_ |
| Kiro Powers listing | _unassigned_ |
| OpenAI (Codex) plugin directory listing | _unassigned_ |
| Support inbox (support@shipyard.build) | _unassigned_ |

Listing owners hold the publisher account for their marketplace and submit every update to it. Record who owns each
account here, by GitHub handle, not by email or credential.

## Releasing a plugin version

1. Open a PR that bumps `version` in the plugin's `plugin.json` and `.claude-plugin/plugin.json` (`hosted/` has its own
   pair), and in `marketplace.json`, if it sets one. CI fails if they differ. Use semver: patch for wording and skill fixes, minor for new behaviour or a
   higher minimum CLI version, major for changes that need user action.
   Bump the version in every release: Claude Code uses it to decide whether an installed plugin is out of date.
2. If the release needs a newer CLI, release the CLI first and raise the minimum version in `README.md` and
   `COMPATIBILITY.md` in the same PR. Those are the only places that state the minimum. The skill (`skills/shipyard/SKILL.md`) only gives
   version-specific advice, such as `shipyard upgrade` needing CLI 1.10.0, so check that advice still holds.
3. Merge after review and a green CI run.
4. Tag the merge commit and publish a GitHub Release with the changes. The root plugin's tags are `vX.Y.Z`; the hosted
   plugin's are `shipyard-hosted-vX.Y.Z`, since its versions are separate and `v0.1.0` is already taken:

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z" <merge-commit> && git push origin vX.Y.Z
   gh release create vX.Y.Z --verify-tag --notes "..."
   ```

   Never move or delete a published tag.
5. Submit the update to every curated listing (Cursor reviews every update by hand). Record the submission and its
   review state on the release's Linear issue.

   For Kiro, open a PR to [kirodotdev/powers](https://github.com/kirodotdev/powers) that replaces its `shipyard/`
   folder with this release's `plugin.json`, `mcp.json` and `skills/`, unchanged. Its README entry changes only if the
   description does. The two copies must stay identical: a fix asked for in Kiro's review lands here first, as a new
   release, then in the PR. Kiro's team decides when it merges.
6. Smoke-test from each listing in a clean profile: install, ask "list my Shipyard environments", check that a
   destructive tool asks first, then uninstall.

## Rolling back

- **Plugin:** revert the change on `main` with a PR that also bumps the version **forward** (for example, from a bad
  0.2.0 to 0.2.1), and release that. A plain `git revert` of a release PR also reverts its version bump: the
  manifests go back to the old version, CI still passes, and Claude Code users already on the bad version never get
  the rollback. Don't re-point tags.
  Self-hosted installs get the fix on their next update. For curated listings, submit the new version; if a
  marketplace lets you unlist or restore a previous version, the listing owner does that while review is pending.
- **CLI:** follow [Rolling back](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#rolling-back) in the
  MCP guide. A bad CLI release is fixed by a new CLI release; the plugin does not pin a CLI version.
- **Hosted MCP server:** release the previous tag, as in
  [Deploying the hosted server](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#deploying-the-hosted-server).
- **Exposed secret:** if a credential ever lands in this repository, rotate it first, then remove it. Rewriting history
  does not un-publish it, and marketplaces may have cached the content.

## Monitoring

| What | Where | Who looks |
|---|---|---|
| Bug reports and install problems | This repository's issues, and shipyard-cli issues | Plugin repository owners |
| User support requests | support@shipyard.build | Support inbox owner |
| Marketplace review results and takedown notices | Each publisher account's email and dashboard | Listing owner |
| CI health on `main` | The `Validate` workflow | Plugin repository owners |
| CLI release health | shipyard-cli's `Release` and weekly `release-check` workflows | CLI release owner |
| Hosted MCP server health | Its pods and ingress logs, and `https://mcp.shipyard.build/mcp` answering 401 without a token | Hosted server deploy owner |
| Known client bugs that need docs workarounds | [kirodotdev/Kiro#11924](https://github.com/kirodotdev/Kiro/issues/11924) (Kiro `installed.json`) | @daniellle |

## Support routing

| Report | Goes to |
|---|---|
| Install, update or "server won't start" | support@shipyard.build, or an issue here. Check the CLI version first (`shipyard --version`) and the [Troubleshooting](https://github.com/shipyard/shipyard-cli/blob/main/docs/mcp.md#troubleshooting) guide |
| Wrong tool behaviour or results | An issue on [shipyard/shipyard-cli](https://github.com/shipyard/shipyard-cli/issues) |
| Security problem | security@shipyard.build, never a public issue. See [SECURITY.md](https://github.com/shipyard/shipyard-cli/blob/main/SECURITY.md) |
| Marketplace policy or review question | The listing owner for that marketplace |
