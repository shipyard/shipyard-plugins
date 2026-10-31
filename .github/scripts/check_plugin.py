"""Repo checks the client validators don't make.

- Two plugins: the root runs the local CLI over stdio (`shipyard mcp serve`);
  hosted/ connects to Shipyard's hosted server over HTTP and signs in with
  OAuth, so it needs no CLI.
- MCP servers are defined only in each plugin's mcp.json and .mcp.json, never
  inline in a plugin or marketplace manifest. A stdio definition carries only
  command/args (and `type` in mcp.json) and runs `shipyard`; a hosted one
  carries only type and url, and the url is Shipyard's. They match across the
  two files.
- No `env` or `headers` key, token-shaped string or token reference anywhere in
  a manifest. Users
  authenticate with `shipyard login`; Codex passes Claude's ${user_config.*}
  placeholders through literally (SHIP-338), so a wired-in token breaks auth.
- name and version match across each plugin's plugin.json and
  .claude-plugin/plugin.json, and the marketplace lists each plugin from its
  own folder.
- Every skills/*/SKILL.md has YAML frontmatter with a name matching its folder
  and a description.

Exits 1 with one line per problem.
"""

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
# Plugin folder -> the transport its server uses.
PLUGINS = {".": "stdio", "hosted": "http"}
HOSTED_URL = "https://mcp.shipyard.build/mcp"
# The server definition's type in each file, per transport: mcp.json is the
# Agent Plugins schema, .mcp.json is Claude Code's.
SERVER_TYPES = {
    "stdio": {"mcp.json": "stdio", ".mcp.json": None},
    "http": {"mcp.json": "streamable-http", ".mcp.json": "http"},
}
SERVER_KEYS = {
    "stdio": {"mcp.json": {"type", "command", "args"}, ".mcp.json": {"command", "args"}},
    "http": {"mcp.json": {"type", "url"}, ".mcp.json": {"type", "url"}},
}


def rel(plugin, name):
    return name if plugin == "." else f"{plugin}/{name}"


SERVER_FILES = {rel(p, n) for p in PLUGINS for n in ("mcp.json", ".mcp.json")}
MANIFESTS = sorted(
    {rel(p, n) for p in PLUGINS for n in ("plugin.json", "mcp.json", ".mcp.json", ".claude-plugin/plugin.json")}
    | {".claude-plugin/marketplace.json"}
)
CREDENTIAL_KEYS = {"env", "envfile", "headers"}

# syt_ user tokens, then any long unbroken token_urlsafe-style run (org API
# tokens and bypass tokens have no prefix). The run must contain an uppercase
# letter, digit or underscore, so long hyphenated words like
# `kubernetes-pull-request-preview-environments` don't match.
TOKEN_VALUE = re.compile(r"syt_[A-Za-z0-9_-]+|(?=[a-z-]*[A-Z0-9_])[A-Za-z0-9_-]{32,}")
# Wiring a token in, checked in every key and value.
TOKEN_REF = re.compile(r"user_config|SHIPYARD_API_TOKEN|api_token", re.IGNORECASE)
# Words that are fine in prose, so they're checked only in keys and server definitions.
TOKEN_WORD = re.compile(r"token|authorization|bearer", re.IGNORECASE)
ARG = re.compile(r"^-{0,2}[a-z][a-z0-9-]*$")

errors = []


def strings(node, path):
    """Yield (path, is_key, key or value) for every key and string value in a JSON tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", True, key
            yield from strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from strings(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, False, node


def load(name):
    try:
        return json.loads((ROOT / name).read_text())
    except (OSError, ValueError) as e:
        errors.append(f"{name}: cannot read JSON: {e}")
        return None


docs = {name: load(name) for name in MANIFESTS}

for name, doc in docs.items():
    if doc is None:
        continue
    for path, is_key, s in strings(doc, name):
        if is_key and s.lower() in CREDENTIAL_KEYS:
            errors.append(f"{path}: `{s}` is not allowed in a manifest; rely on `shipyard login` or the hosted server's OAuth sign-in")
        if is_key and s == "mcpServers" and not (name in SERVER_FILES and path == f"{name}.mcpServers"):
            errors.append(f"{path}: define MCP servers only in mcp.json and .mcp.json")
        if path.endswith(".$schema") and not is_key:
            continue
        in_server = ".mcpServers" in path
        if TOKEN_VALUE.search(s) or TOKEN_REF.search(s) or ((is_key or in_server) and TOKEN_WORD.search(s)):
            errors.append(f"{path}: {s!r} looks like a credential or token reference; rely on `shipyard login` or the hosted server's OAuth sign-in")

for plugin, transport in PLUGINS.items():
    servers = {}
    for base, allowed in SERVER_KEYS[transport].items():
        name = rel(plugin, base)
        doc = docs[name]
        if doc is None:
            continue
        defs = doc.get("mcpServers")
        if not isinstance(defs, dict) or set(defs) != {"shipyard"}:
            errors.append(f"{name}: mcpServers must define exactly one server, `shipyard`")
            continue
        server = defs["shipyard"]
        extra = set(server) - allowed
        if extra:
            errors.append(f"{name}: unexpected keys {sorted(extra)} in the shipyard server (allowed: {sorted(allowed)})")
        want_type = SERVER_TYPES[transport][base]
        if transport == "stdio":
            if server.get("type", "stdio") != "stdio":
                errors.append(f"{name}: shipyard server type must be stdio")
            if server.get("command") != "shipyard":
                errors.append(f"{name}: shipyard server command must be `shipyard`")
            args = server.get("args")
            if not isinstance(args, list) or not all(isinstance(a, str) and ARG.match(a) for a in args):
                errors.append(f"{name}: args must be plain subcommands or flags without values, got {args!r}")
            servers[base] = {k: server.get(k) for k in ("command", "args")}
        else:
            if server.get("type") != want_type:
                errors.append(f"{name}: shipyard server type must be {want_type!r}, got {server.get('type')!r}")
            if server.get("url") != HOSTED_URL:
                errors.append(f"{name}: shipyard server url must be {HOSTED_URL}, got {server.get('url')!r}")
            servers[base] = {"url": server.get("url")}
    if len(servers) == 2 and servers["mcp.json"] != servers[".mcp.json"]:
        errors.append(
            f"{rel(plugin, 'mcp.json')} and {rel(plugin, '.mcp.json')} define different servers: "
            f"{servers['mcp.json']} vs {servers['.mcp.json']}"
        )

    agent, claude = docs[rel(plugin, "plugin.json")], docs[rel(plugin, ".claude-plugin/plugin.json")]
    if agent and claude:
        for field in ("name", "version"):
            if agent.get(field) != claude.get(field):
                errors.append(
                    f"{field} differs: {rel(plugin, 'plugin.json')} {agent.get(field)!r}, "
                    f"{rel(plugin, '.claude-plugin/plugin.json')} {claude.get(field)!r}"
                )
    market = docs[".claude-plugin/marketplace.json"]
    if market and claude:
        entries = [e for e in market.get("plugins", []) if e.get("name") == claude.get("name")]
        source = "./" if plugin == "." else f"./{plugin}"
        if len(entries) != 1:
            errors.append(f"marketplace.json must list {claude.get('name')!r} once, from {source}")
        for entry in entries:
            if entry.get("source") != source:
                errors.append(f"marketplace.json lists {claude.get('name')!r} from {entry.get('source')!r}, not {source}")
            if "version" in entry and entry["version"] != claude.get("version"):
                errors.append(
                    f"marketplace.json version {entry['version']!r} differs from {rel(plugin, '.claude-plugin/plugin.json')}"
                )

skills = []
for plugin in PLUGINS:
    found = sorted((ROOT / plugin).glob("skills/*/SKILL.md"))
    if not found:
        errors.append(f"{rel(plugin, 'skills/*/SKILL.md')}: none found")
    skills += found
for skill in skills:
    rel = skill.relative_to(ROOT)
    parts = skill.read_text().split("---", 2)
    if len(parts) < 3 or parts[0].strip():
        errors.append(f"{rel}: must start with YAML frontmatter between --- lines")
        continue
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        errors.append(f"{rel}: invalid YAML frontmatter: {e}")
        continue
    if not isinstance(meta, dict):
        errors.append(f"{rel}: frontmatter must be a mapping")
        continue
    if meta.get("name") != skill.parent.name:
        errors.append(f"{rel}: name {meta.get('name')!r} must match its folder {skill.parent.name!r}")
    if not isinstance(meta.get("description"), str) or not meta["description"].strip():
        errors.append(f"{rel}: description is required")

for e in errors:
    print(f"::error::{e}")
sys.exit(1 if errors else 0)
