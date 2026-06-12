# code-creator

Generate a draft dbt model from a CNGBA Jira ticket: fetch ticket → get schema → create branch + PR → post Jira comment.

## Usage

```
/code-creator CNGBA-123
```

---

## Instructions

You are the Code Creator agent. Execute the following steps in order. Stop immediately if any step fails — do not proceed to later steps or post a Jira comment unless all prior steps succeed.

### Step 1 — Parse ticket ID

Extract the ticket ID from `$ARGUMENTS` (e.g. `CNGBA-123`).
If no argument is provided, ask the user for a ticket ID and stop.

### Step 2 — Fetch ticket

```bash
python scripts/fetch_ticket.py <TICKET_ID>
```

- If **exit code ≠ 0**: print the stderr output to the user and stop.
- If stdout contains `"Skipping:"`: print that line to the user and stop. Do not continue.
- Otherwise: parse stdout as JSON. This is the **ticket object**.

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
python scripts/get_schema.py <catalog.schema.table>
```
Merge its output into the schema object. Note the fallback to the user.

If no tables were found in Step 3, use `{}` as the schema object.

### Step 5 — Create branch and Draft PR

Write the ticket and schema objects to temp files, then run:
```bash
python scripts/create_branch.py --ticket /tmp/cc_ticket.json --schema /tmp/cc_schema.json
```

- If exit code ≠ 0: print the error. **Do not post a Jira comment.** Stop.
- Capture the last line of stdout — this is the **PR URL**.
- Note the `SQL:` path printed to stderr — this is the **sql_path**.

### Step 6 — Post Jira comment

Use the Jira MCP tool (`addCommentToJiraIssue`) to post a comment on the ticket:

> Branch `feature/<TICKET_ID>` opened. Draft model at `<sql_path>`. PR: <PR_URL>. Ready for your review.

Use the `cloudId` from the `JIRA_BASE_URL` environment variable (the hostname portion, e.g. `moodysanalytics.atlassian.net`).

### Step 7 — Print summary

Output to chat:
- Ticket ID and summary
- Domain and layer inferred (from create_branch.py stderr)
- SQL file path
- PR URL
- "Jira comment posted ✓"
