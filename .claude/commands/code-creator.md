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
Build a space-separated list. If none found, use an empty list and note it to the user.

### Step 4 — Fetch schema metadata

If table references were found:
```bash
python scripts/get_schema.py <table1> <table2> ...
```

If no tables found, run with a single placeholder:
```bash
python scripts/get_schema.py none
```

- If exit code ≠ 0: print the error and stop.
- Parse stdout as JSON. This is the **schema object**.

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
