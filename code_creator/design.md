# Code Creator Agent — Design Document

> Status: Production-ready prototype. Demonstrated end-to-end on CNGBA-590.

---

## Agent Summary (Presentation Reference)

### What It Does
A Claude Code agent that reads a Jira ticket, retrieves source table schemas from Databricks Unity Catalog, generates a production-ready dbt SQL model with real business logic, commits it to a GitHub feature branch, and notifies the assignee — all from a single slash command: `/code-creator CNGBA-123`.

---

### Inputs

| Input | Source | How Acquired |
|---|---|---|
| Jira ticket ID | Engineer (slash command arg) | Manual: `/code-creator CNGBA-590` |
| Ticket summary, description, assignee, status | Jira REST API v3 | Fetched automatically via `fetch_ticket.py` |
| Source table schemas (column names, types, descriptions) | Databricks Unity Catalog dev | Queried automatically via Databricks MCP |
| Source table schemas (PRD-only tables) | `kyc-data-pipelines` GitHub repo | Fetched automatically via GitHub Contents API when UC unavailable |
| Existing dbt model patterns | `kyc-data-pipelines` GitHub repo | Referenced by Claude during SQL generation |

---

### Outputs

| Output | Location | Description |
|---|---|---|
| Feature branch | `moodysanalytics/kyc-data-pipelines` | `feature/CNGBA-<id>`, branched from live `dev` HEAD |
| Draft dbt SQL model | `kyc_dbt_platform/models/{domain}/{layer}/{model}.sql` | Full `{{ config() }}` block + business logic (CTEs, aggregations, filters, `{{ ref() }}`/`{{ source() }}`) |
| `schema.yml` entry | Same folder as SQL file | Model output columns with descriptions and `unique`/`not_null` data tests |
| Jira comment | Original ticket | Branch name + file path, notifying the assignee |

---

### Business Impact

| Metric | Manual Process | With Agent | Saving |
|---|---|---|---|
| Time to scaffold a new dbt model | 2–4 hours (schema lookup, branch creation, boilerplate, Jira update) | ~2 minutes end-to-end | **~95% reduction per ticket** |
| Schema accuracy for PRD-only tables | Manual Jira/Confluence research | Automatically inferred from existing dbt model on GitHub | Eliminates lookup errors |
| Config block compliance | Engineer checks 8 required meta tags manually | Always generated with all 8 tags | Zero missing-tag defects |
| Idempotency / double-processing | Risk of duplicate branches if ticket re-triggered | GitHub branch check gate — skips if already processed | Zero duplicate branches |
| Time from ticket creation to code in branch | Hours to days (engineer availability) | Minutes (async, no engineer needed for scaffolding) | Faster review cycle |

**Demonstrated on CNGBA-590:** Agent inferred EVA domain + gold layer from ticket text, retrieved source schema via GitHub fallback (UC inaccessible), generated correct 5-metric aggregation model (`total_requests`, `completed_requests`, `failed_requests`, `failure_rate_pct`) with month-grain grouping and internal-traffic filter — matching the business logic described in the ticket — in under 2 minutes.

---

### Scalability

- **Across domains:** Works for all 10 business domains (`eva`, `screening`, `grid`, `maxsight`, `orbis`, `client_usage`, `quantexa`, `ml_logging`, `npi`, `eecr`) — domain inferred automatically from ticket text, no per-domain configuration
- **Across catalog environments:** UC dev queried first; GitHub fallback handles PRD-only tables automatically — no engineer intervention required
- **Across workflows:** Interactive (`/code-creator` slash command) and CI/CD (GitHub Actions cron, v2 roadmap) share the same scripts — no logic duplication
- **Across projects:** Jira project key is configurable; the gate logic (status, keyword, idempotency) is project-agnostic
- **Teammate onboarding:** Clone repo, fill `.env`, run immediately — no server, no install beyond Python deps

---

### Agentic Components

| Component | Tool / MCP | Role |
|---|---|---|
| Schema retrieval | Databricks MCP (`get_table_stats_and_schema`) | Queries Unity Catalog dev — authenticated via workspace profile, no extra credentials |
| Schema fallback | GitHub Contents API + Claude SQL parser | Fetches dbt model file tree, downloads matching `.sql`, infers columns from final SELECT |
| Business logic generation | Claude (LLM reasoning) | Reads ticket description, extracts metrics/filters/aggregations, generates dbt SQL body |
| Branch + commit | GitHub REST API (`create_branch.py`) | Creates branch from live dev HEAD, commits SQL + schema.yml via Contents API |
| Assignee notification | Atlassian MCP (`addCommentToJiraIssue`) | Posts branch name + file path back to the Jira ticket |

**Agentic pattern:** Multi-tool orchestration — Jira API → Databricks MCP → GitHub API (read) → LLM generation → GitHub API (write) → Atlassian MCP. Each tool is used for what it does best; Claude owns only the reasoning step.

---

## Overview

An agent that reads a Jira ticket, retrieves source schema from Databricks Unity Catalog (with GitHub fallback for PRD-only tables), generates a production-ready dbt SQL model with real business logic, creates a feature branch, and notifies the assignee. A separate PR Review agent handles final review before merge.

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
- **Fallback for missing tables:** If a table referenced in the ticket doesn't exist in dev, `get_schema.py` fetches the `kyc_dbt_platform/models/` file tree from the `dev` branch on GitHub (using `GITHUB_TOKEN`), finds a matching `.sql` file by name, downloads it, and infers column names from the final SELECT. No local clone of kyc-data-pipelines is required. Fallback is logged clearly so the engineer knows types will be `unknown`.
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

## Phase 4: GitHub Branch & Draft Files

### Branch Naming
```
feature/<TICKET-ID>     e.g.  feature/PROJ-123
```
Branched from: `dev` (live HEAD SHA fetched at runtime)

### Agent Creates
- Feature branch via GitHub API
- Commits draft SQL file and `schema.yml` entry to the branch
- Posts Jira comment with branch name and file path

### Agent Does NOT Create
- A Pull Request — the assignee opens the PR manually after reviewing the draft

### Handoff to Assignee
- Assignee checks out `feature/CNGBA-<id>` in VS Code
- Reviews and iterates on the draft SQL and schema.yml
- Runs `dbt run` + `dbt test` on Databricks
- Opens PR manually when ready
- PR Review agent (existing) takes over

### Configuration
- **GitHub org:** `moodysanalytics`, **repo:** `kyc-data-pipelines`
- **dbt SQL fallback:** `get_schema.py` fetches the model file tree directly from GitHub — no local clone or `KYC_REPO_PATH` needed

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
| 14 | Databricks MCP (`get_table_stats_and_schema`) used for schema lookup in interactive slash command | Already authenticated via `kyc-data-dev` profile — no extra env vars; faster than spawning a subprocess |
| 15 | `get_schema.py` retained as the CI/CD path for v2 scheduled runs | GitHub Actions cannot use the local MCP connection; script is portable and credential-injectable via secrets |
| 16 | MCP fallback: if a table is absent from MCP response, call `get_schema.py` for that table only | Maintains the dbt-SQL-parsing fallback for PRD-only tables without duplicating logic |
| 17 | MCP config in `.mcp.json` using `DATABRICKS_CONFIG_PROFILE=kyc-data-dev` | Standard Claude Code project MCP pattern; teammates with the same `.ai-dev-kit` setup get the connection automatically on clone |
| 18 | Branch always created from live dev HEAD SHA fetched at runtime via GitHub API | Ensures feature branch is never stale regardless of when the agent runs |
| 19 | Temp files use OS temp dir (not hardcoded `/tmp/`) | `/tmp/` doesn't exist on Windows; use `tempfile` or `$TEMP` so the agent works on all platforms |
| 20 | Jira comment posted via Atlassian MCP (`addCommentToJiraIssue`), not a script | MCP is already configured for Jira; avoids a fourth script and a second set of Jira credentials |
| 21 | Agent does NOT open a PR — assignee opens it manually after reviewing the draft | Keeps the agent's scope narrow; PR creation is a human decision, not an agent decision |
| 22 | `get_schema.py` GitHub fallback: fetches repo file tree via GitHub API and downloads matching `.sql` file | No local clone required; always reads current `dev` branch; works in CI/CD; `KYC_REPO_PATH` removed |

---

## Build Status (2026-06-11)

All five plan steps are complete and pushed to `parthdoshi28/pr-sheriff`.

| File | Status |
|------|--------|
| `scripts/fetch_ticket.py` | Done — Jira fetch, 3 gates (status, dbt keyword, idempotency) |
| `scripts/get_schema.py` | Done — UC query via `databricks-sql-connector`, dbt SQL fallback |
| `scripts/create_branch.py` | Done — domain/layer inference, SQL + schema.yml codegen, Draft PR |
| `.claude/commands/code-creator.md` | Done — MCP-first schema lookup, script fallback |
| `.mcp.json` | Done — Databricks MCP wired to `kyc-data-dev` profile |
| `.env.example` / `.gitignore` / `README.md` | Done |

### Definition of Done — outstanding items

- [ ] End-to-end test against a real CNGBA ticket
- [ ] Second invocation of same ticket → exits cleanly ("already processed")
- [ ] Ticket with no "dbt" mention → exits cleanly with skip reason
- [ ] Ticket referencing a PRD-only table → schema inferred from dbt model SQL, flagged in generated output
