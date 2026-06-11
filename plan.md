# Code Creator — Build Plan

> Reference this alongside `design.md`. Design = what & why. Plan = build order, steps, and done criteria.

---

## Build Order

```
1. fetch_ticket.py
2. get_schema.py
3. create_branch.py
4. .claude/commands/code-creator.md
5. .env.example + README
```

Each step is independently testable before the next begins.

---

## Step 1 — `scripts/fetch_ticket.py`

### What it does
Fetches a CNGBA Jira ticket and decides whether to proceed.

### Concrete build steps
1. Accept ticket ID as CLI arg (`python fetch_ticket.py CNGBA-123`)
2. Call Jira REST API `GET /rest/api/3/issue/{id}` with Basic auth (email + API token)
3. Extract: `summary`, `description`, `status.name`, `assignee.displayName`
4. Gate 1 — status check: if status not in `["To Do", "In Progress"]` → print reason, `sys.exit(0)`
5. Gate 2 — dbt keyword check: case-insensitive search for `"dbt"` in summary + description → if not found → print reason, `sys.exit(0)`
6. Gate 3 — idempotency: call GitHub API `GET /repos/moodysanalytics/kyc-data-pipelines/branches/feature/{id}` → if branch exists → print "already processed", `sys.exit(0)`
7. On pass: print structured JSON to stdout (consumed by slash command) and `sys.exit(0)` with code `0`

### Acceptance criteria
| Scenario | Expected behaviour |
|----------|--------------------|
| Valid CNGBA ticket, status "In Progress", description contains "dbt", no branch exists | Prints JSON with summary, description, assignee; exit 0 |
| Ticket status is "Done" | Prints "Skipping: status is Done, not To Do/In Progress"; exit 0 |
| Description has no mention of "dbt" | Prints "Skipping: no dbt keyword found"; exit 0 |
| Branch `feature/CNGBA-123` already exists on GitHub | Prints "Skipping: already processed (branch exists)"; exit 0 |
| Invalid ticket ID / 404 from Jira | Prints error message with status code; exit 1 |
| Missing env vars | Prints which var is missing; exit 1 |

---

## Step 2 — `scripts/get_schema.py`

### What it does
Given a list of table references, returns column metadata from Unity Catalog dev — falling back to dbt model SQL if a table is absent in dev.

### Concrete build steps
1. Accept table list as CLI arg: space-separated `catalog.schema.table` strings
2. Connect to Databricks dev via `databricks-sql-connector` using `.env` credentials
3. For each table: query `{catalog}.information_schema.columns` — select `column_name`, `data_type`, `comment` ordered by `ordinal_position`
4. If table not found (empty result or exception): scan `kyc_dbt_platform/models/` for a `.sql` file whose name contains the table name; parse SELECT column list to infer names (types listed as `unknown`)
5. Tag every column with `"source": "unity_catalog"` or `"source": "dbt_model_inferred"`
6. Print structured JSON to stdout

### Acceptance criteria
| Scenario | Expected behaviour |
|----------|--------------------|
| Table exists in dev UC | Returns columns with types and UC descriptions; source = `unity_catalog` |
| Table exists in PRD only (absent in dev) | Falls back to dbt model SQL; returns column names; types = `unknown`; source = `dbt_model_inferred`; logs fallback warning |
| Table not in dev AND no matching dbt model | Returns entry with `"columns": [], "source": "not_found"`; does not crash |
| Databricks connection fails | Prints connection error; exit 1 |
| No tables passed | Prints usage hint; exit 1 |

---

## Step 3 — `scripts/create_branch.py`

### What it does
Generates draft dbt SQL + schema.yml entry, pushes them to a new feature branch, and opens a Draft PR.

### Concrete build steps
1. Accept ticket JSON (from Step 1) + schema JSON (from Step 2) as stdin or file args
2. Infer domain by keyword-matching ticket text against known domain list (`screening`, `maxsight`, `grid`, `eva`, `orbis`, `client_usage`, `quantexa`, `ml_logging`, `npi`, `eecr`); if ambiguous, print candidates and prompt user to pick one
3. Determine layer from ticket text (`bronze`, `silver`, `gold`); default to `silver` if unclear
4. Construct target file paths:
   - SQL: `kyc_dbt_platform/models/{domain}/{layer}/{domain}_{layer}_{descriptor}.sql`
   - Schema: `kyc_dbt_platform/models/{domain}/{layer}/schema.yml`
5. Generate SQL file:
   - `{{ config(...) }}` block with all 8 required tags (values inferred from ticket; `originator`/`maintainer` = assignee)
   - CTEs using `{{ source() }}` or `{{ ref() }}` based on table origins
   - Surrogate key as `MD5(CONCAT_WS('||', ...))` if model needs one
   - Column list explicit (no `SELECT *`)
6. Generate schema.yml addition:
   - Model entry with description
   - All columns from schema JSON with descriptions
   - `unique` + `not_null` on surrogate key; `dbt_utils.unique_combination_of_columns` for composite keys
7. Via GitHub API:
   a. Get `dev` branch HEAD SHA: `GET /repos/moodysanalytics/kyc-data-pipelines/git/ref/heads/dev`
   b. Create branch: `POST /repos/.../git/refs` → `feature/CNGBA-<id>`
   c. Commit SQL file (create or update blob + tree + commit)
   d. Commit schema.yml update (append new model entry to existing file)
   e. Create Draft PR: title = `CNGBA-<id>: <ticket summary>`, body = Jira URL + one-line description
8. Print PR URL to stdout

### Acceptance criteria
| Scenario | Expected behaviour |
|----------|--------------------|
| Happy path end-to-end | Branch `feature/CNGBA-<id>` exists on GitHub; SQL file present at correct path; schema.yml updated; PR is Draft with correct title and Jira URL in body |
| SQL file uses `SELECT *` | Must not happen — column list always explicit |
| `{{ config() }}` block missing any of the 8 tags | Must not happen — all 8 always generated (empty string if unknown) |
| Domain ambiguous (ticket mentions two domains) | Prompts user to choose before proceeding |
| Layer not determinable from ticket | Defaults to `silver`, logs assumption |
| GitHub API rate-limited or auth fails | Prints error with HTTP status; exit 1 |

---

## Step 4 — `.claude/commands/code-creator.md`

### What it does
Orchestrating slash command: runs Steps 1–3 in sequence, then posts a Jira comment.

### Concrete build steps
1. Parse ticket ID from slash command argument (`/code-creator CNGBA-123`)
2. Run `python scripts/fetch_ticket.py <id>` — capture stdout JSON; if exit non-zero or output empty, stop and report to user
3. Run `python scripts/get_schema.py <tables>` (tables extracted from ticket JSON) — capture schema JSON
4. Run `python scripts/create_branch.py` with ticket + schema JSON — capture PR URL
5. Post Jira comment via API: `"Branch \`feature/CNGBA-<id>\` opened. Draft model at \`<sql path>\`. PR: <PR URL>. Ready for your review."`
6. Print summary to Claude Code chat

### Acceptance criteria
| Scenario | Expected behaviour |
|----------|--------------------|
| Full run on valid ticket | Chat shows: domain inferred, files created, PR URL, Jira comment posted |
| fetch_ticket.py exits with skip | Claude prints the skip reason; no further steps run; no Jira comment |
| create_branch.py fails | Claude prints error; Jira comment NOT posted (avoids false "done" signal) |
| User runs `/code-creator CNGBA-123` a second time | fetch_ticket.py catches idempotency; Claude prints "already processed"; exits cleanly |

---

## Step 5 — `.env.example` + `README`

### Concrete build steps
1. Write `.env.example` with all required vars, inline comments explaining where to find each value
2. Write `README.md` with: prerequisites, setup (clone → fill `.env` → install deps), usage (`/code-creator CNGBA-123`), and a one-paragraph explanation of what the agent does

### Acceptance criteria
| Check | Pass condition |
|-------|---------------|
| `.env` not committed | `.gitignore` contains `.env` |
| All vars in `.env.example` are used somewhere in scripts | No orphaned or missing vars |
| README setup steps work on a clean clone | A new teammate can run the agent following only the README |

---

## Definition of Done (full agent)

- [ ] End-to-end test: `/code-creator <real CNGBA ticket>` → branch created, Draft PR opened, Jira comment posted, SQL + schema.yml files are valid dbt syntax
- [ ] Second invocation of same ticket → exits cleanly with "already processed"
- [ ] A ticket with no "dbt" mention → exits cleanly with skip reason
- [ ] A ticket referencing a table absent in dev UC → schema inferred from dbt model, clearly flagged in generated SQL comments
- [ ] No credentials appear in any committed file
- [ ] `python -m py_compile scripts/*.py` passes (no syntax errors)
