# Code Creator Agent

An AI agent that turns a CNGBA Jira ticket into a draft dbt model. Given a ticket ID, it fetches the ticket from Jira, reads relevant table schemas from Databricks Unity Catalog, generates a dbt SQL file and `schema.yml` entry following the repo's conventions, pushes them to a new `feature/CNGBA-<id>` branch on GitHub, opens a Draft PR, and posts a summary comment back to the Jira ticket — all from a single slash command.

---

## Prerequisites

- Python 3.9+
- [Claude Code](https://docs.anthropic.com/en/claude/claude-code) (VS Code extension or CLI)
- Access to: Jira (CNGBA project), Databricks dev workspace, GitHub (`moodysanalytics/kyc-data-pipelines`)

Install the one Python dependency:

```bash
pip install databricks-sql-connector
```

---

## Setup

1. Clone this repo (or copy this directory alongside the `kyc-data-pipelines` repo):
   ```
   kyc-data-pipelines/     ← existing repo
   code-creator/           ← this directory
   ```

2. Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   # edit .env with your Jira email/token, Databricks token, and GitHub token
   ```

3. Load the `.env` file in your shell (or set the variables in your environment):
   ```bash
   export $(grep -v '^#' .env | xargs)
   ```

4. Open this directory in VS Code with the Claude Code extension active.

---

## Usage

In the Claude Code chat panel:

```
/code-creator CNGBA-123
```

Claude will:
1. Fetch the ticket and check it is `To Do` or `In Progress` and mentions `dbt`
2. Look for Unity Catalog table references in the ticket description and retrieve column metadata
3. Infer the domain (`screening`, `maxsight`, `grid`, etc.) and layer (`bronze`/`silver`/`gold`) from ticket text
4. Generate a dbt SQL file and `schema.yml` entry following repo conventions
5. Push them to `feature/CNGBA-123` branched from `dev` and open a Draft PR
6. Post a comment on the Jira ticket with the branch and PR link

If the branch already exists, Claude exits cleanly with "already processed" — safe to re-run.

---

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/fetch_ticket.py` | Fetch ticket from Jira, apply gates (status, dbt keyword, idempotency), output JSON |
| `scripts/get_schema.py` | Query Unity Catalog for column metadata; fall back to dbt model SQL if table is absent in dev |
| `scripts/create_branch.py` | Generate SQL + schema.yml, push to GitHub feature branch, open Draft PR |

Each script can be run independently for debugging:

```bash
python scripts/fetch_ticket.py CNGBA-123
python scripts/get_schema.py kyc_aws_us_dev_silver_catalog.kyc.my_table
python scripts/create_branch.py --ticket /tmp/ticket.json --schema /tmp/schema.json
```

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `JIRA_BASE_URL` | Atlassian instance URL, e.g. `https://moodysanalytics.atlassian.net` |
| `JIRA_EMAIL` | Your Atlassian account email |
| `JIRA_API_TOKEN` | Jira API token (not your password) |
| `DATABRICKS_HOST` | Databricks workspace URL |
| `DATABRICKS_HTTP_PATH` | SQL warehouse HTTP path |
| `DATABRICKS_ACCESS_TOKEN` | Databricks personal access token |
| `GITHUB_TOKEN` | GitHub PAT with `Contents` and `Pull requests` write scope |
| `GITHUB_REPO` | Target repo in `org/repo` format |
| `GITHUB_BASE_BRANCH` | Branch to create features from (default: `dev`) |
