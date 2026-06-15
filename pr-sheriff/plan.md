# Plan: `pr-sheriff` Skill

## 1. Problem

When editing or reviewing a dbt model (`.sql`) in a Databricks-backed dbt repo,
four classes of bugs slip through:

1. The model drifts from team conventions documented in the repo's root
   `CLAUDE.md` (style, naming, required config, forbidden patterns).
2. The model `{{ ref(...) }}`s or `{{ source(...) }}`s tables that don't
   actually exist in `models/` or `sources.yml`.
3. The model's declared environment (catalog/schema in `{{ config(...) }}` or
   model `.yml` `meta`/`tags`) is inconsistent with the env of the things it
   references - e.g. a `prod` model reading from a `dev` source. Conversely,
   the model may be missing env-tags that all its dependencies support,
   meaning it could be safely promoted to additional environments.
4. The model references columns (`alias.column`) on a ref'd or source'd table
   that don't actually exist in that table's schema YAML `columns:` declaration
   - e.g. a typo like `orders.cusotmer_id` or a stale column that was renamed.

This skill catches all four in a single, on-demand check, with no warehouse
connection required.

## 2. Goal

Build a personal Cursor skill (`~/.cursor/skills/pr-sheriff/`) that,
when invoked on a target `.sql` file, produces a structured report:

- ✅ / ❌ for each `CLAUDE.md` rule
- ✅ / ❌ for each `ref()` and `source()` reference (exists in repo files)
- ✅ / ❌ for env consistency between the target and every referenced object
- ⚠ for promotion opportunities (envs all dependencies support but target lacks)
- ✅ / ❌ for each qualified column reference against declared columns in
  schema/source YAML
- ❌ for any backtick-quoted column alias containing spaces (breaks dbt)

The skill is **explicitly invoked** (`disable-model-invocation: true`) - it
runs only when the user names it.

## 3. Non-Goals (v1)

- No live Databricks queries. Existence is checked against project files only
  (`models/**/*.sql`, `**/sources.yml`, `**/schema.yml`).
- No `dbt compile` / `manifest.json` dependency. Pure static parsing.
- No auto-fix. The skill reports; the agent (or user) fixes.
- No support for non-Databricks warehouses in v1 (config keys assume `catalog`
  + `schema`).
- No multi-repo / monorepo CLAUDE.md merging. Single root `CLAUDE.md`.
- Column existence checks only cover *qualified* references (`alias.column`).
  Bare column names without a table prefix are skipped (ambiguous without full
  SQL semantic analysis). Referenced models' SELECT clauses are not parsed.

## 4. Inputs & Outputs

**Input**: absolute or repo-relative path to a single `.sql` file inside a
dbt project.

**Output**: a markdown report with four sections (CLAUDE rules, References,
Env consistency, Column checks), each item tagged `PASS` / `FAIL` / `WARN`,
plus an overall verdict.

## 5. Deliverables

| # | Artifact | Location |
|---|----------|----------|
| 1 | `plan.md` (this file) | `pr-sheriff/` |
| 2 | `design.md` | `pr-sheriff/` |
| 3 | `SKILL.md` | `pr-sheriff/` |
| 4 | Validator scripts | `pr-sheriff/scripts/` |
| 5 | Report template | `pr-sheriff/report-template.md` |

## 6. Milestones

1. **M1 - Plan & Design approved** (this doc + `design.md`).
2. **M2 - Skill scaffold** (`SKILL.md` + directory).
3. **M3 - Reference & env checkers** (file-scan based, fully offline).
4. **M3b - Column checker** (alias mapping, YAML column lookup, alias naming).
5. **M4 - CLAUDE.md rule loader** (reads root `CLAUDE.md`, extracts checkable
   rules into a checklist for the agent).
6. **M5 - End-to-end dry run** against a sample dbt model.
7. **M6 - (Optional) Promote to project skill** (`.cursor/skills/...`) once
   the team confirms behaviour.

## 7. Risks & Open Questions

- **CLAUDE.md rules are prose, not regex.** Most rules need agent judgement,
  not a script. Plan: scripts handle the *mechanical* checks (refs, sources,
  env, columns); the agent reads `CLAUDE.md` itself and applies it to the SQL.
- **Env signal ambiguity.** A model may set `catalog` in `config()` but rely
  on profile defaults in dev. We treat *missing* env signals as `WARN`, not
  `FAIL`, and surface them.
- **dbt repo root discovery.** We walk up from the target `.sql` looking for
  `dbt_project.yml`. If not found, the skill errors out cleanly.
- **Databricks naming.** Catalog/schema casing varies. Comparisons are
  case-insensitive.
- **Column declarations may be absent.** Not all models/sources have a
  `columns:` section in their YAML. When absent, the check emits `WARN`
  rather than `FAIL` since the contract is unknown.
- **Unqualified column references.** Bare `column_name` without an alias
  prefix cannot be mapped to a specific ref/source without full SQL semantic
  analysis. These are skipped in v1.

## 8. Success Criteria

- Running the skill on a known-good model returns all `PASS`.
- Running on a model with a broken `ref()` flags exactly that ref.
- Running on a `prod`-configured model that refs a `dev`-only source flags
  the env mismatch.
- Running on a model with `{us_prd}` whose refs all support `{us_prd, eu_prd}`
  surfaces the `eu_prd` promotion opportunity.
- Running on a model that uses `orders.cusotmer_id` where the schema YAML
  declares `customer_id` flags the column mismatch.
- Running on a model with `` AS `Institution Name` `` flags the bad alias.
- Total runtime < 5 seconds on a repo with ~500 models.
- Zero false positives on the first 5 real models the user tries.
