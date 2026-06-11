# Code Creator Agent — Design Document

> Status: In progress. Decisions locked through Phase 1–2. Phases 3–5 pending dbt repo review.

---

## Overview

An agent that monitors Jira for new tickets, detects dbt model changes, drafts dbt-compatible SQL using Databricks Unity Catalog context and existing dbt model patterns, opens a feature branch on GitHub, and posts a summary comment back to the ticket. A separate PR Review agent (already built) handles final review before merge.

---

## Architecture

```
code-creator/
├── .claude/
│   └── commands/
│       └── code-creator.md   ← Claude Code slash command
├── scripts/
│   ├── fetch_ticket.py       ← Jira API: fetch and classify ticket
│   ├── get_schema.py         ← Databricks: query Unity Catalog metadata
│   └── create_branch.py      ← GitHub API: open feature branch
├── .env.example              ← credential template (committed)
├── .env                      ← real credentials (gitignored)
└── design.md
```

**Runtime:** Claude Code (VS Code extension or CLI)
**Trigger:** Manual (`/code-creator PROJ-123`) or scheduled (GitHub Actions cron — future)
**Shareable:** Clone repo, fill `.env`, run immediately

---

## Phase 1: Ticket Ingestion & Classification

### Trigger
- **Manual (v1):** `/code-creator <TICKET-ID>` slash command in Claude Code
- **Scheduled (v2):** GitHub Actions cron, runs twice daily at 12:00 and 17:00 EST

### Classification Logic
1. Fetch ticket title + description via Jira REST API
2. Check for explicit `dbt` mention (case-insensitive keyword match)
3. If found → proceed to Phase 2
4. If not found → log and exit (no tokens spent on generation)

### Jira Actions
- Read: ticket title, description, assignee
- Write: post a comment when agent completes (e.g., "Branch `feature/PROJ-123` opened. Draft model at `models/marts/fct_orders.sql`. Ready for review.")

### Configuration
- **Project key:** `CNGBA` only (scope may expand later)
- **Statuses:** `To Do` and `In Progress` only
- **Idempotency:** Agent runs once per ticket. Guard: before doing any work, check GitHub for an existing `feature/CNGBA-<id>` branch. If it exists → log "already processed" and exit. No local state file needed — GitHub is the source of truth.

---

## Phase 2: Databricks Context Retrieval

### What the Agent Fetches
- Table and column names from Unity Catalog
- Column descriptions and metadata via `information_schema` or `DESCRIBE EXTENDED`
- Used to understand source data available for the dbt model

### Access Method
- Databricks SQL via `databricks-sql-connector` (Python)
- Queries `system.information_schema` or catalog-specific schemas
- Credentials via `.env` (host, HTTP path, access token)

### Configuration
- **Target environment:** Databricks dev (`kyc_aws_us_dev` / `kyc_aws_eu_dev`) — PRD not yet accessible
- **Catalogs queried:** `kyc_aws_us_dev_bronze_catalog`, `kyc_aws_us_dev_silver_catalog`, `kyc_aws_us_dev_gold_catalog`, and domain-specific catalogs (e.g., `kyc_aws_us_dev_client_usage_catalog`)
- **Fallback for missing tables:** If a table referenced in the ticket doesn't exist in dev, `get_schema.py` falls back to parsing the corresponding dbt model SQL from the repo to infer column names and types. Fallback is logged clearly so the engineer knows it's inferred, not authoritative.
- **Metadata only** — no sample rows fetched. Avoids PII exposure; column names + types + UC descriptions are sufficient for generation.

---

## Phase 3: dbt Code Generation

### Context Sources
1. **Databricks Unity Catalog** — available tables, columns, descriptions (Phase 2)
2. **Existing dbt model SQL** — pulled from GitHub repo to match patterns, layer conventions, and Jinja macros in use

### Code Generation Steps
1. Parse ticket to identify: source tables, target model name, business logic described
2. Fetch relevant UC metadata for those tables
3. Fetch similar existing dbt models from repo for pattern reference
4. Draft SQL using dbt conventions (refs, sources, tests)
5. Create/update `schema.yml` entry with column descriptions

### Confirmed from dbt Repo Review (2026-06-10)

**Folder structure** — NOT `staging/intermediate/marts`. This repo uses a domain-based medallion layout:
```
models/
├── {domain}/
│   ├── bronze/
│   ├── silver/
│   └── gold/
└── sources/          ← source definitions, one subdir per source system
```
Domains in use: `client_usage`, `eecr`, `eva`, `grid`, `maxsight`, `ml_logging`, `npi`, `orbis`, `quantexa`, `screening`.

**Naming convention** — `{domain}_{layer}_{descriptor}.sql`. Examples:
- `client_usage_bronze_combined_client_identifier.sql`
- `maxsight_silver_usage_eva_region_tier_table.sql`
- `m4c_eva_consumption_tracking.sql` (special `m4c_` prefix for M4C reporting initiative)

**Required Jinja patterns in every new model:**
```sql
{{ config(
    tags=["us_prd", ...],                -- env + domain tags
    meta={
      "originator": "...",
      "maintainer": "...",
      "layer": "bronze|silver|gold",
      "sensitivity": "...",
      "compliance": "...",
      "use_case": "...",
      "refresh_freq_and_ingest": "...",
      "consumption": "..."
    }
) }}
```
- `{{ source('system', 'table') }}` for external sources
- `{{ ref('model_name') }}` for internal dbt models
- Conditional enablement for env-specific models: `enabled=(target.name in ['kyc_aws_us_prd', ...])`
- Surrogate keys: `MD5(CONCAT_WS('||', col1, col2, ...))` pattern
- Lookup tables carry semver versioning: `{% set lookup_version = '1.0.0' %}` + columns `lookup_version`, `effective_from`

**Schema.yml conventions:**
- Multiple models share one `schema.yml` per layer folder (not one file per model)
- Primary key test pattern: `data_tests: [unique, not_null]` on surrogate key; `dbt_utils.unique_combination_of_columns` for composite business keys
- Every column must have a `description` (enforced by CLAUDE.md)
- Accepted values tests used for enum-like columns (e.g., tier codes)
- Conditional uniqueness: `unique: { where: "col is not null" }` where applicable

**Sources location:** `models/sources/` — one subdirectory per source system (eecr, eva, grid, maxsight, snowflake_crdb, etc.)

**Catalog / environment naming:**
- Catalogs: `{{ env_var('DBT_ENVIRONMENT') }}_[bronze|silver|gold|client_usage|...]_catalog`
- Targets: `kyc_aws_{us|eu}_{dev|stg|prd}`
- Tags drive which models run per target: `us_dev`, `eu_prd`, etc.

### Remaining Open Questions
- [x] Which domain(s) will the agent generate models for? → **All domains; domain is inferred from ticket content.**
  - Inference order: (1) exact keyword match on domain names in ticket title/description (`screening`, `maxsight`, `grid`, `eva`, `orbis`, `client_usage`, `quantexa`, `ml_logging`, `npi`, `eecr`); (2) if ambiguous or missing, agent prompts user to confirm before proceeding.
  - Domain → folder mapping is a config dict in `get_schema.py` / command prompt (not hardcoded per-domain scripts).

---

## Phase 4: GitHub Branch & PR

### Branch Naming
```
feature/<TICKET-ID>     e.g.  feature/PROJ-123
```
Branched from: `dev`

### Agent Creates
- Feature branch via GitHub API
- Commits draft SQL file(s) and `schema.yml` changes
- Opens a Draft PR with ticket title as PR title and ticket URL in description

### Handoff to Assignee
- Assignee opens the branch in VS Code, runs `dbt run` + `dbt test` on Databricks
- Iterates on the draft; agent can be re-invoked to refine
- When ready, assignee marks PR as "Ready for Review"
- PR Review agent (existing) takes over

### Configuration
- **GitHub org:** `moodysanalytics`, **repo:** `kyc-data-pipelines`
- **PR title format:** `CNGBA-<id>: <one-line summary from ticket title>`
- **PR body:** Ticket URL + one-sentence description. No special format required — the PR Review agent has no hard parsing dependency on the description.
- **Jira ticket link:** Yes, included in PR body automatically.

---

## Phase 5: Configuration & Onboarding

### `.env.example`
```
# Jira
JIRA_BASE_URL=https://your-org.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=

# Databricks
DATABRICKS_HOST=https://your-workspace.azuredatabricks.net
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/your-warehouse-id
DATABRICKS_ACCESS_TOKEN=

# GitHub
GITHUB_TOKEN=
GITHUB_REPO=org/repo-name
GITHUB_BASE_BRANCH=dev

# Agent behavior
JIRA_PROJECT_KEYS=PROJ,DATA
POLL_SCHEDULE=12:00,17:00 EST
```

### Onboarding Steps (to be added to README)
1. Clone repo
2. Copy `.env.example` to `.env`, fill in credentials
3. Open repo in VS Code with Claude Code extension
4. Run `/code-creator <TICKET-ID>` to test

---

## Decisions Log

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | dbt detection via free-text keyword match on "dbt" | Labels not yet in use; explicit "dbt" mention is reliable signal |
| 2 | Manual trigger first, scheduled poll second | Ship fast for demo; scheduled adds zero rework later |
| 3 | Scheduled poll twice daily (noon + 5pm EST) | Low token cost; only dbt tickets trigger generation |
| 4 | Claude Code slash command as runtime | No server, no install friction; generalizes to CLI cron |
| 5 | UC metadata + existing dbt SQL as generation context | Gives agent source schema awareness and pattern consistency |
| 6 | Branch from `dev`, open Draft PR | Matches standard gitflow; keeps PRs auditable |
| 7 | Repo-shareable via `.env` config | Other teammates can clone and run; no hardcoded credentials |
| 8 | Domain inferred from ticket keywords, not scoped to one domain | Agent should work across all 10 domains; ambiguous tickets prompt user to confirm |
| 9 | CNGBA project only, To Do + In Progress statuses | Focused scope for v1; designed to expand later |
| 10 | Idempotency via GitHub branch check | If `feature/CNGBA-<id>` already exists, skip — no local state file needed |
| 11 | UC metadata from dev; fall back to dbt model SQL for missing tables | PRD not yet accessible; fallback keeps agent functional for tables that only exist in PRD |
| 12 | Metadata only, no sample rows | Avoids PII exposure; column names + types + descriptions are sufficient context |
| 13 | PR title = `CNGBA-<id>: <brief summary>`; body = ticket URL + one sentence | No special format needed by PR Review agent; keeps it simple |

---

## What's Next

1. **Open dbt repo in VS Code** — agent reads structure to finalize Phase 3 conventions
2. **Confirm Jira project keys and ticket statuses** to watch
3. **Confirm Databricks catalog/schema scope**
4. **Build `fetch_ticket.py`** — Jira fetch + classify
5. **Build `get_schema.py`** — UC metadata query
6. **Build `create_branch.py`** — GitHub branch + draft PR
7. **Write `.claude/commands/code-creator.md`** — orchestrating slash command
8. **Write `.env.example` and README**
