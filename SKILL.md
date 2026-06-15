---
name: pr-sheriff
description: Validate a dbt .sql model against the repo's root CLAUDE.md rules, verify every ref()/source() resolves to a real model or sources.yml entry, confirm the target's environment (catalog/schema/tags) matches every referenced object, and check that qualified column references resolve to declared columns in schema/source YAML. Use when the user names this skill or asks to "validate", "lint", "compliance check", or "review" a dbt SQL file against CLAUDE.md, or to check that referenced tables exist / are in the same env / have the right columns. Databricks-flavored, file-scan only (no warehouse connection, no manifest.json required).
disable-model-invocation: true
---

# PR Sheriff

Validates a single dbt `.sql` model on four axes:

1. **CLAUDE.md compliance** - root `CLAUDE.md` rules applied to the SQL.
2. **Reference existence** - every `{{ ref(...) }}` and `{{ source(...) }}`
   resolves to a real file (`models/**/*.sql` or `sources.yml`).
3. **Env consistency (env-tags)** - two sub-checks:
   - **Subset rule**: the target's deployment envs (encoded as tags of the
     shape `<region>_<env>`, e.g. `us_prd`, `eu_prd`, `us_dev`, `eu_dev` in
     the `{{ config(tags=[...]) }}` block) must be a **subset** of each
     referenced object's env-tags. If the target ships to an env the ref
     doesn't, that's a `FAIL` - the join will break in that env.
   - **Promotion opportunities**: if ALL resolved refs/sources share env-tags
     that the target does not have, those envs are safe promotion targets.
     Emitted as `WARN` so the user knows the target could be deployed more
     broadly.
4. **Column checks** - two sub-checks:
   - **Existence**: every qualified column reference (`alias.column`) in the
     SQL resolves to a column declared in the referenced model's schema YAML
     or source YAML `columns:` section. Only qualified references are checked;
     bare column names without a table prefix are skipped. If a ref/source has
     no `columns:` declared in YAML, the check emits `WARN`.
   - **Alias naming**: backtick-quoted column aliases containing spaces
     (e.g. `` AS `Institution Name` ``) are flagged as `FAIL` because they
     break dbt compilation. Column aliases must be valid identifiers without
     spaces.

Databricks-flavored. Offline only - no dbt compile, no warehouse calls.

### Env-tag convention

Env-tags match the regex `^[a-z]{2,5}_(prd|prod|dev|stg|staging|uat|qa|test)$`
(case-insensitive). Examples that count: `us_prd`, `eu_prd`, `apac_dev`,
`gb_uat`. Examples that don't (treated as ordinary tags): `gold`, `marts`,
`daily`, `client`. The `databricks_tags={...}` dict is metadata, **not**
an env signal, and is ignored by env checks.

## When to run

The user invokes this skill explicitly with a target `.sql` path. Required
input:

- `TARGET`: absolute or workspace-relative path to a dbt model `.sql` file.

If `TARGET` is missing, ask for it before doing anything else.

## Prerequisites

- Python 3.9+ on PATH.
- `pyyaml` available (`pip install pyyaml`). If missing, the agent should
  install it before running scripts.
- *(Optional)* Databricks MCP server configured in Cursor — enables a live
  table-existence fallback when `check_refs.py` can't find a ref/source in
  the local repo files. See [`SETUP.md`](SETUP.md) for configuration
  instructions.

## Workflow

Copy this checklist and track progress as you go:

```
- [ ] Step 1: Discover project (dbt_project.yml, CLAUDE.md, file index)
- [ ] Step 2: Parse target (refs, sources, env signal)
- [ ] Step 3: Check references exist
- [ ] Step 3c: MCP fallback for any unresolved refs/sources (skip if no Databricks MCP configured)
- [ ] Step 3b: Check column existence
- [ ] Step 4: Check env consistency
- [ ] Step 5: Read CLAUDE.md and apply rules to the SQL
- [ ] Step 6: Render report from report-template.md
```

Let `SKILL_DIR` be the directory containing this `SKILL.md` (e.g.
`~/.cursor/skills/pr-sheriff/` when installed as a personal skill). All
scripts below live in `<SKILL_DIR>/scripts/`.

### Step 1 - Discover project

```bash
python <SKILL_DIR>/scripts/discover_project.py "$TARGET"
```

Emits JSON: `{project_root, claude_md, models[], source_yamls[], schema_yamls[]}`.
Exits non-zero if no `dbt_project.yml` is found walking up from `TARGET`.

### Step 2 - Parse target

```bash
python <SKILL_DIR>/scripts/parse_target.py "$TARGET" "<project_root>"
```

Emits JSON: `{refs[], sources[], env: {catalog, schema, tags, meta_env}}`.

### Step 3 - Check references

```bash
python <SKILL_DIR>/scripts/check_refs.py <parsed.json> <discovery.json>
```

Emits JSON list of `{kind, raw, status: PASS|FAIL|WARN, detail, resolved_env}`.

### Step 3c - MCP fallback (optional)

**Skip this step if no Databricks MCP server is configured in Cursor.**

For each row from Step 3 with `status: FAIL`, attempt a live lookup against
Unity Catalog using the Databricks MCP tool:

1. Determine the table name:
   - For `ref('<name>')`: use `<name>`.
   - For `source('<src>', '<tbl>')`: use `<tbl>`.
2. Determine catalog/schema from parsed env (`catalog` and `schema` fields
   from Step 2 output), if available.
3. Select the MCP server whose name best matches the target's env-tags
   (e.g. a model tagged `us_dev` prefers `databricks-dev` or
   `databricks-us-dev`). If no match, fall back to any configured
   `databricks-*` server.
4. Use the MCP SQL execution tool to run:
   ```sql
   SELECT table_catalog, table_schema, table_name
   FROM system.information_schema.tables
   WHERE lower(table_name) = lower('<table_name>')
   LIMIT 10
   ```
   Optionally narrow by `table_schema` and `table_catalog` if known from the
   parsed env.
5. Apply the result:
   - **Found**: upgrade that row's status from `FAIL` to `WARN` and note
     `"not in repo files, found in Databricks at <catalog.schema.table_name>"`.
   - **Not found**: keep as `FAIL`, note
     `"not found in repo files or Databricks catalog"`.
   - **MCP error**: keep as `FAIL`, note `"MCP lookup failed: <error message>"`.

### Step 3b - Check columns

```bash
python <SKILL_DIR>/scripts/check_columns.py "$TARGET" <parsed.json> <refs.json> <discovery.json>
```

Emits JSON list of `{kind, raw, alias, column, line, status: PASS|FAIL|WARN, detail}`.
The script performs two checks:

1. **Column existence** - builds an alias-to-ref/source map from the SQL,
   extracts qualified column references (`alias.column`), then checks each
   against the `columns:` declarations in schema YAML (for models) or source
   YAML (for sources). If a ref/source has no `columns:` declared, the check
   emits `WARN` rather than `FAIL`.
2. **Alias naming** - scans for backtick-quoted column aliases with spaces
   (e.g. `` AS `Institution Name` ``). These break dbt and are emitted as
   `FAIL` with `kind: "alias_naming"`.

### Step 4 - Check env consistency

```bash
python <SKILL_DIR>/scripts/check_env.py <parsed.json> <refs.json>
```

Emits JSON list of `{kind, raw, line, status, detail}`. Includes per-ref
subset checks and, at the end, any promotion opportunities (`kind: "promotion"`)
where all dependencies support envs the target doesn't deploy to.

> **Tip**: pipe each stdout to a temp file under `$env:TEMP` (Windows) or
> `/tmp` (unix) so steps 3, 3b, and 4 can read them without re-running.

### Step 5 - Apply CLAUDE.md rules

This step is **agent-driven**, not scripted. Read
`<project_root>/CLAUDE.md` and apply the rules in it to the target SQL.

**Rule-extraction rubric**:

1. Skip narrative/intro sections.
2. For every imperative sentence ("Always …", "Never …", "Models must …",
   "Use snake_case …"), turn it into one checklist item.
3. Classify each item:
   - **Mechanical** (regex / lookup confirms it) → check directly. PASS or
     FAIL with evidence (line number + snippet).
   - **Judgement** (intent-dependent) → check, but mark `WARN` when
     uncertain rather than `FAIL`.
4. **Never invent rules not present in `CLAUDE.md`**.
5. If `CLAUDE.md` is missing, the entire section is a single `WARN`
   ("CLAUDE.md not found at project root").

### Step 6 - Render report

Fill `report-template.md` with the results from steps 3, 3b, 4, 5. Output the
final report inline in chat. Use ✅/❌/⚠ glyphs only here in the final
report (never inside scripts or earlier agent text).

## Output contract

The final report **must** include:

- Overall verdict (`PASS` / `FAIL` / `WARN`) on the first line after the
  title.
- Four numbered sections (CLAUDE.md, References, Env consistency, Column
  checks).
- A summary line with counts at the bottom.

Verdict rule:

- Any `FAIL` → overall `FAIL`.
- Else any `WARN` → overall `WARN`.
- Else `PASS`.

## Error handling

- Target file missing → stop, tell the user.
- No `dbt_project.yml` found → stop, tell the user the file isn't inside a
  dbt project.
- `pyyaml` missing → install it, then continue.
- Malformed YAML in a `sources.yml` → script will skip and emit a `WARN`;
  surface it in the report.

## Files

- `scripts/discover_project.py` - locate project root and file inventory.
- `scripts/parse_target.py` - extract refs/sources/env from the SQL +
  sibling YAML.
- `scripts/check_refs.py` - resolve refs/sources against project files.
- `scripts/check_env.py` - compare target env vs each referenced object.
- `scripts/check_columns.py` - verify qualified column references against
  schema/source YAML declarations and flag backtick-quoted aliases with spaces.
- `report-template.md` - markdown template for the final report.
