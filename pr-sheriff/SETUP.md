# PR Sheriff — Optional MCP Setup

**pr-sheriff runs offline with no credentials.** This file covers optional Databricks MCP configuration only.

For Jira / GitHub / Databricks credentials (code-creator and dbt-workflow), see [SETUP.md](../SETUP.md) at the repo root.

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.9+ | Must be on `PATH` |
| `pyyaml` | `pip install pyyaml` |
| Cursor **or** Claude Code | Cursor (GUI) or Claude Code CLI (`claude`) — see installation below |
| Databricks MCP *(optional)* | Enables live table-existence fallback — see below |

---

## Installation

This skill is one of three in the [dbt agent skills](../README.md) repo. Install the **pr-sheriff** subdirectory, not the repo root.

### Cursor

Clone the repo, symlink this skill, then **restart Cursor**:

**Mac / Linux**
```bash
git clone https://github.com/parthdoshi28/pr-sheriff.git ~/.cursor/skills/dbt-agent-skills
ln -s ~/.cursor/skills/dbt-agent-skills/pr-sheriff ~/.cursor/skills/pr-sheriff
```

**Windows (PowerShell)**
```powershell
git clone https://github.com/parthdoshi28/pr-sheriff.git "$env:USERPROFILE\.cursor\skills\dbt-agent-skills"
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.cursor\skills\pr-sheriff" -Target "$env:USERPROFILE\.cursor\skills\dbt-agent-skills\pr-sheriff"
```

To confirm it loaded: open the command palette and type `pr-sheriff` — the skill should appear.

### Claude Code

Clone the repo, symlink this skill, then **restart Claude Code**:

**Mac / Linux**
```bash
git clone https://github.com/parthdoshi28/pr-sheriff.git ~/.claude/skills/dbt-agent-skills
ln -s ~/.claude/skills/dbt-agent-skills/pr-sheriff ~/.claude/skills/pr-sheriff
```

**Windows (PowerShell)**
```powershell
git clone https://github.com/parthdoshi28/pr-sheriff.git "$env:USERPROFILE\.claude\skills\dbt-agent-skills"
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.claude\skills\pr-sheriff" -Target "$env:USERPROFILE\.claude\skills\dbt-agent-skills\pr-sheriff"
```

To confirm it loaded: type `/pr-sheriff` in the chat — Claude Code should recognize the skill.

---

## Usage

### Cursor

```
Use pr-sheriff to validate models/marts/my_model.sql
```

### Claude Code

```
/pr-sheriff models/marts/my_model.sql
```

The skill will run all four checks and print a structured report with `PASS` / `FAIL` / `WARN` verdicts.

---

## Optional: Databricks MCP fallback

Without MCP, pr-sheriff is fully offline. It validates refs/sources by scanning files in the local repo. If a `ref()` or `source()` target isn't found in the repo (because it lives in another repo, is externally managed, or is just not cloned locally), the check returns `FAIL`.

With a Databricks MCP server configured, the skill will automatically query Unity Catalog for any unresolved table and downgrade the verdict to `WARN` if the table is found there.

### Configure the MCP server

The JSON config is the same for both tools — only the file path differs.

#### Cursor — `~/.cursor/mcp.json`

Create or edit `~/.cursor/mcp.json`:

#### Claude Code — `~/.claude/mcp.json`

Create or edit `~/.claude/mcp.json`:

**Config content (same for both):**

```json
{
  "mcpServers": {
    "databricks-dev": {
      "type": "streamable-http",
      "url": "https://<YOUR_DEV_WORKSPACE>.cloud.databricks.com/api/2.0/mcp/functions/system/ai",
      "headers": {
        "Authorization": "Bearer <YOUR_DEV_TOKEN>"
      }
    },
    "databricks-prd": {
      "type": "streamable-http",
      "url": "https://<YOUR_PRD_WORKSPACE>.cloud.databricks.com/api/2.0/mcp/functions/system/ai",
      "headers": {
        "Authorization": "Bearer <YOUR_PRD_TOKEN>"
      }
    }
  }
}
```

A ready-to-copy template is available in [`mcp-config-template.json`](mcp-config-template.json).

**Naming convention**: the skill infers which MCP server to use from the target SQL's env-tags (e.g. a model tagged `us_dev` will prefer a server named `databricks-dev` or `databricks-us-dev`). If no match is found, it falls back to any configured `databricks-*` server.

### Get a Databricks personal access token

1. Open your Databricks workspace.
2. Click your username (top right) → **Settings** → **Developer** → **Access tokens**.
3. Click **Generate new token**, give it a name and expiry, copy the value.
4. Paste it into the `Authorization: Bearer <token>` header in the MCP config above.

For production use, consider a service principal token instead of a personal one. See the [Databricks token docs](https://docs.databricks.com/en/dev-tools/auth/pat.html) for details.

### Verify MCP is working

After adding the config, restart your tool. Then run the skill against a model that has external dependencies:

- **Cursor**: `Use pr-sheriff to validate models/marts/some_model_with_external_deps.sql`
- **Claude Code**: `/pr-sheriff models/marts/some_model_with_external_deps.sql`

If the MCP connection is working, refs to tables outside the local repo will show `⚠` (WARN) instead of `❌` (FAIL) in the References section, with a note indicating the table was found in Databricks.

---

## Running checks manually

All scripts can be run individually from the `pr-sheriff/scripts/` directory for debugging:

```bash
# Full pass in one shot
python pr-sheriff/scripts/_run_all.py path/to/model.sql

# Individual scripts (each prints JSON to stdout)
python pr-sheriff/scripts/discover_project.py path/to/model.sql
python pr-sheriff/scripts/parse_target.py path/to/model.sql /path/to/dbt/project
python pr-sheriff/scripts/check_refs.py parsed.json discovery.json
python pr-sheriff/scripts/check_columns.py path/to/model.sql parsed.json refs.json discovery.json
python pr-sheriff/scripts/check_env.py parsed.json refs.json
```
