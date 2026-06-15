# dbt Agent Skills — Setup

One-time setup before using **code-creator** or **dbt-workflow**. **pr-sheriff** is standalone and does not need any of this.

## Which skill needs what?

| Skill | Credentials | Network | Notes |
|-------|-------------|---------|-------|
| **pr-sheriff** | None | Offline | Needs Python 3.9+ and `pyyaml` only |
| **code-creator** | Jira, Databricks, GitHub | Yes | Also needs Databricks + Jira MCP in Cursor |
| **dbt-workflow** | Same as code-creator | Yes | Runs code-creator then pr-sheriff |

---

## Step 1 — Python dependencies

```bash
pip install pyyaml databricks-sql-connector
```

- `pyyaml` — pr-sheriff
- `databricks-sql-connector` — code-creator schema fallback (`get_schema.py`)

---

## Step 2 — Credential setup (code-creator / dbt-workflow only)

Run the interactive wizard from the repo root (or from `code-creator/`):

```bash
python code-creator/scripts/setup_credentials.py
```

It prompts for:

| Variable | Used for |
|----------|----------|
| `JIRA_BASE_URL` | Atlassian instance URL |
| `JIRA_EMAIL` | Your Atlassian email |
| `JIRA_API_TOKEN` | [Atlassian API token](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `DATABRICKS_HOST` | Databricks workspace URL |
| `DATABRICKS_HTTP_PATH` | SQL warehouse HTTP path |
| `DATABRICKS_ACCESS_TOKEN` | Databricks personal access token |
| `GITHUB_TOKEN` | [GitHub PAT](https://github.com/settings/tokens) with Contents + Pull requests write |
| `GITHUB_REPO` | Target repo (`org/repo`) |
| `GITHUB_BASE_BRANCH` | Branch to cut features from (default: `dev`) |

Credentials are written to `code-creator/.env` (gitignored). Scripts load these automatically when present.

### Verify setup

```bash
python code-creator/scripts/check_setup.py
```

Exit code `0` and `"status": "PASS"` means you can run code-creator or dbt-workflow.

If setup is incomplete, the agent must **stop** and ask you to run `setup_credentials.py` before continuing.

### Re-run or update credentials

Run `setup_credentials.py` again. Press Enter on a field to keep an existing non-secret value.

---

## Step 3 — Cursor MCP (code-creator / dbt-workflow only)

The agent uses MCP tools in addition to `.env`:

| MCP tool | Purpose |
|----------|---------|
| Databricks `get_table_stats_and_schema` | Pull Unity Catalog column metadata |
| Jira `addCommentToJiraIssue` | Post branch summary on the ticket |

Configure Databricks MCP in Cursor (profile or token pointing at your dev workspace). See [pr-sheriff/SETUP.md](pr-sheriff/SETUP.md) for optional Databricks MCP used by pr-sheriff's ref fallback — that is separate and optional.

---

## Step 4 — Install skills

See [README.md](README.md) for cloning and symlinking `code-creator/`, `pr-sheriff/`, and `dbt-workflow/`.

---

## Quick reference

```bash
# Offline validation — no setup needed beyond pyyaml
Use pr-sheriff to validate path/to/model.sql

# Online skills — run setup first
python code-creator/scripts/setup_credentials.py
python code-creator/scripts/check_setup.py
Use code-creator for CNGBA-123
Use dbt-workflow for CNGBA-123
```

---

## pr-sheriff-only setup

If you only use **pr-sheriff**, skip Steps 2–3. Optional Databricks MCP for live ref fallback: [pr-sheriff/SETUP.md](pr-sheriff/SETUP.md).
