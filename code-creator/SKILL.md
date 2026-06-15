---
name: code-creator
description: Generate a draft dbt model from a CNGBA Jira ticket — fetch ticket, pull Unity Catalog schema via Databricks MCP, create a feature branch with SQL and schema.yml, and post a Jira comment. Use when the user names this skill, provides a CNGBA ticket ID, or asks to create/generate a dbt model from a Jira ticket.
disable-model-invocation: true
---

# Code Creator

Generate a draft dbt model from a CNGBA Jira ticket: fetch ticket → get schema → create branch with draft files → post Jira comment.

## When to run

The user invokes this skill explicitly with a ticket ID. Required input:

- `TICKET_ID`: e.g. `CNGBA-123`

If `TICKET_ID` is missing, ask for it before doing anything else.

**Standalone:** pr-sheriff validates existing `.sql` files offline with no credentials.
Use **dbt-workflow** to create and validate in one run.

## Step 0 — Verify credential setup (required)

Before any other step, run:

```bash
python <SKILL_DIR>/scripts/check_setup.py
```

- If `"status": "PASS"` → continue to Step 1.
- If `"status": "FAIL"` → **stop**. Tell the user to run:
  ```bash
  python <SKILL_DIR>/scripts/setup_credentials.py
  ```
  Or walk them through [SETUP.md](../SETUP.md). Do not fetch tickets or call GitHub until setup passes.

Scripts load credentials from `<SKILL_DIR>/.env` automatically.

## Prerequisites

- Python 3.9+ on PATH.
- Credentials configured via `setup_credentials.py` (see [SETUP.md](../SETUP.md)).
- `databricks-sql-connector` (`pip install databricks-sql-connector`) for schema fallback.
- Databricks MCP server configured in Cursor for live schema lookup.
- Jira MCP tool available for posting comments.

Let `SKILL_DIR` be the directory containing this `SKILL.md`. All scripts live in `<SKILL_DIR>/scripts/`.

## Workflow

Copy this checklist and track progress:

```
- [ ] Step 0: Verify credential setup (check_setup.py)
- [ ] Step 1: Parse ticket ID
- [ ] Step 2: Fetch ticket
- [ ] Step 3: Extract table references
- [ ] Step 4: Fetch schema metadata via Databricks MCP
- [ ] Step 5: Generate SQL body and output column list
- [ ] Step 6: Create branch and commit draft files
- [ ] Step 7: Post Jira comment
- [ ] Step 8: Print summary
```

Stop immediately if any step fails — do not proceed to later steps or post a Jira comment unless all prior steps succeed.

### Step 1 — Parse ticket ID

Extract the ticket ID from the user's message (e.g. `CNGBA-123`).
If no ticket ID is provided, ask the user and stop.

### Step 2 — Fetch ticket

```bash
python <SKILL_DIR>/scripts/fetch_ticket.py <TICKET_ID>
```

- If **exit code ≠ 0**: print the stderr output to the user and stop.
- If stdout contains `"Skipping:"`: print that line to the user and stop. Do not continue.
- Otherwise: parse stdout as JSON. Save to `<tmpdir>/cc_ticket.json`. This is the **ticket object**.

### Step 3 — Extract table references

Scan the ticket object's `description` field for Unity Catalog table references.
Look for patterns like `catalog.schema.table` (three dot-separated identifiers) and plain UC table names.
Build a list of unique `catalog.schema.table` strings. If none found, note it to the user and use an empty schema object `{}`.

### Step 4 — Fetch schema metadata via Databricks MCP

For each unique `catalog.schema` pair in the table list, call the Databricks MCP tool:

```
get_table_stats_and_schema(
  catalog = "<catalog>",
  schema  = "<schema>",
  table_names = ["<table>"],
  table_stat_level = "NONE"
)
```

Build a **schema object** in this exact structure (required by `create_branch.py`):

```json
{
  "catalog.schema.table": {
    "columns": [
      {"name": "col_name", "type": "data_type", "description": "comment or empty string", "source": "unity_catalog"}
    ],
    "source": "unity_catalog"
  }
}
```

Map the MCP response fields: `name` → `name`, `data_type`/`type` → `type`, `comment` → `description`.

**If a table is not returned by the MCP** (absent in dev UC), fall back to `get_schema.py` for that table only:

```bash
python <SKILL_DIR>/scripts/get_schema.py <catalog.schema.table>
```

Merge its output into the schema object. Note the fallback to the user.
Save the final schema object to `<tmpdir>/cc_schema.json`.

If no tables were found in Step 3, use `{}` as the schema object.

### Step 5 — Generate SQL body and output column list

Read the ticket description carefully. Generate two files:

**A. SQL body → `<tmpdir>/cc_sql_body.sql`** (CTEs + SELECT logic, no config block):

- Look for explicit SQL snippets in the ticket description — adapt them to dbt style as the starting point
- Extract all named metrics, filter conditions, aggregations, and GROUP BY columns described in plain English
- Use `{{ ref('model_name') }}` for internal dbt models, `{{ source('system', 'table') }}` for raw sources — never hardcode catalog/schema paths
- The SELECT must reflect actual business logic (aggregations, filters, derived columns, ratios) — a flat column dump is not acceptable
- Columns must be explicit — no `SELECT *`
- Do NOT include the `{{ config() }}` block — `create_branch.py` adds that

**B. Output column list → `<tmpdir>/cc_output_columns.json`** — a JSON array listing exactly the columns the model SELECTs (not the source table columns):

```json
[
  {"name": "month_year", "description": "Calendar month in yyyy-MM format"},
  {"name": "total_requests", "description": "Total API requests excluding internal Moodys traffic"}
]
```

If the ticket's business logic is ambiguous, ask the user one clarifying question and stop until answered.

### Step 6 — Create branch and commit draft files

Get the OS temp directory:

```bash
python -c "import tempfile, os; print(tempfile.gettempdir())"
```

Then run:

```bash
python <SKILL_DIR>/scripts/create_branch.py --ticket <tmpdir>/cc_ticket.json --schema <tmpdir>/cc_schema.json --sql-content <tmpdir>/cc_sql_body.sql --output-columns <tmpdir>/cc_output_columns.json
```

- If exit code ≠ 0: print the error. **Do not post a Jira comment.** Stop.
- Note the `SQL:` and `Schema:` paths printed to stderr — these are the **sql_path** and **schema_path**.

### Step 7 — Post Jira comment

Use the Jira MCP tool (`addCommentToJiraIssue`) to post a comment on the ticket:

> Branch `feature/<TICKET_ID>` created. Draft model at `<sql_path>`. Open a PR manually when ready.

Use the `cloudId` from the `JIRA_BASE_URL` environment variable (the hostname portion, e.g. `moodysanalytics.atlassian.net`).

### Step 8 — Print summary

Output to chat:

- Ticket ID and summary
- Domain and layer inferred (from create_branch.py stderr)
- SQL file path and schema.yml path
- Branch name
- Brief description of the business logic generated (metrics, filters, aggregations)
- "Jira comment posted ✓"

Suggest running **pr-sheriff** on `sql_path` next if the user wants validation.

## Files

- `scripts/fetch_ticket.py` — fetch ticket from Jira, apply gates, output JSON
- `scripts/get_schema.py` — Unity Catalog column metadata with dbt SQL fallback
- `scripts/create_branch.py` — generate SQL + schema.yml, push feature branch
- `scripts/setup_credentials.py` — interactive credential wizard
- `scripts/check_setup.py` — verify `.env` before running
- `.env.example` — template (use setup_credentials.py instead)

See [SETUP.md](../SETUP.md) and [README.md](README.md) for setup details.
