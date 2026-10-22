"""Repo checks the client validators don't make.

- MCP server definitions carry only command/args (and `type` in mcp.json), run
  `shipyard`, hold no credentials, and match across mcp.json and .mcp.json.
- No token-shaped string or token reference anywhere in a manifest. Users
  authenticate with `shipyard login`; Codex passes Claude's ${user_config.*}
  placeholders through literally (SHIP-338), so a wired-in token breaks auth.
- name and version match across plugin.json and .claude-plugin/plugin.json.
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
MANIFESTS = ["plugin.json", "mcp.json", ".mcp.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"]
SERVER_KEYS = {"mcp.json": {"type", "command", "args"}, ".mcp.json": {"command", "args"}}

# syt_ user tokens, then any long unbroken token_urlsafe-style run (org API
# tokens and bypass tokens have no prefix).
TOKEN_VALUE = re.compile(r"syt_[A-Za-z0-9_-]+|[A-Za-z0-9_-]{32,}")
TOKEN_REF = re.compile(r"user_config|SHIPYARD_API_TOKEN|api_token|token|authorization|bearer", re.IGNORECASE)
ARG = re.compile(r"^-{0,2}[a-z][a-z0-9-]*$")

errors = []


def strings(node, path):
    """Yield (path, key or value) for every key and string value in a JSON tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", key
            yield from strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from strings(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


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
    for path, s in strings(doc, name):
        if path.endswith(".$schema"):
            continue
        if TOKEN_VALUE.search(s) or TOKEN_REF.search(s):
            errors.append(f"{path}: {s!r} looks like a credential or token reference; rely on `shipyard login`")

servers = {}
for name, allowed in SERVER_KEYS.items():
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
    if server.get("type", "stdio") != "stdio":
        errors.append(f"{name}: shipyard server type must be stdio")
    if server.get("command") != "shipyard":
        errors.append(f"{name}: shipyard server command must be `shipyard`")
    args = server.get("args")
    if not isinstance(args, list) or not all(isinstance(a, str) and ARG.match(a) for a in args):
        errors.append(f"{name}: args must be plain subcommands or flags without values, got {args!r}")
    servers[name] = {k: server.get(k) for k in ("command", "args")}

if len(servers) == 2 and servers["mcp.json"] != servers[".mcp.json"]:
    errors.append(f"mcp.json and .mcp.json launch different commands: {servers['mcp.json']} vs {servers['.mcp.json']}")

agent, claude = docs["plugin.json"], docs[".claude-plugin/plugin.json"]
if agent and claude:
    for field in ("name", "version"):
        if agent.get(field) != claude.get(field):
            errors.append(f"{field} differs: plugin.json {agent.get(field)!r}, .claude-plugin/plugin.json {claude.get(field)!r}")
market = docs[".claude-plugin/marketplace.json"]
if market and claude:
    for entry in market.get("plugins", []):
        if entry.get("name") == claude.get("name") and "version" in entry and entry["version"] != claude.get("version"):
            errors.append(f"marketplace.json version {entry['version']!r} differs from .claude-plugin/plugin.json")

skills = sorted(ROOT.glob("skills/*/SKILL.md"))
if not skills:
    errors.append("no skills/*/SKILL.md found")
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
