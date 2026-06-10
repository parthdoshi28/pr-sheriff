# Design: `pr-sheriff` Skill

Companion to `plan.md`. This doc fixes the *how*.

## 1. High-level architecture

```
User invokes skill on  models/marts/foo.sql
        │
        ▼
SKILL.md workflow (agent driven)
        │
        ├─ 1. discover_project.py      → finds dbt_project.yml, CLAUDE.md
        ├─ 2. parse_target.py          → extracts refs, sources, env from .sql + sibling .yml
        ├─ 3. check_refs.py            → resolves every ref/source against project files
        ├─ 3b. check_columns.py        → verifies column refs + flags bad alias naming
        ├─ 4. check_env.py             → compares target env vs referenced objects' envs
        ├─ 5. (agent step)             → reads CLAUDE.md, applies rules to the SQL
        └─ 6. render report            → fills report-template.md
```

Scripts emit **JSON** to stdout. The agent stitches the JSON results into the
final markdown report. JSON keeps boundaries clean and avoids brittle text
parsing by the agent.

## 2. Directory layout

```
pr-sheriff/
├── SKILL.md
├── plan.md
├── design.md
├── report-template.md
└── scripts/
    ├── discover_project.py
    ├── parse_target.py
    ├── check_refs.py
    ├── check_columns.py
    └── check_env.py
```

All scripts: Python 3.9+, stdlib only (`pathlib`, `re`, `json`, `argparse`,
`yaml` via `pyyaml` - listed as the single dep). PowerShell-safe (no shell
quoting tricks, all args passed positionally).

## 3. dbt project discovery

`discover_project.py <target_sql>`:

1. Walk up from `target_sql` until a directory containing `dbt_project.yml`
   is found. That's `project_root`.
2. Look for `CLAUDE.md` at `project_root/CLAUDE.md`. If absent, return
   `claude_md: null` (the agent will note it as a `WARN`).
3. Glob `project_root/models/**/*.sql` and `project_root/**/sources.yml`
   plus `project_root/**/schema.yml` (yml files capture model-level meta).

Output:
```json
{
  "project_root": "/abs/path",
  "claude_md": "/abs/path/CLAUDE.md",
  "models": ["models/marts/foo.sql", ...],
  "source_yamls": ["models/staging/sources.yml", ...],
  "schema_yamls": ["models/marts/_schema.yml", ...]
}
```

## 4. Target parsing

`parse_target.py <target_sql> <project_root>`:

### 4.1 Refs & sources

Regex (Jinja-tolerant, whitespace-flexible):

- `ref`:    `{{\s*ref\(\s*['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?\s*\)\s*}}`
  - Captures `(name)` and optional `(package_or_version)` - second group
    ignored in v1.
- `source`: `{{\s*source\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*}}`
  - Captures `(source_name, table_name)`.

Comments are stripped first: line comments `--...` and block `/* ... */`,
plus Jinja comments `{# ... #}`. (We do *not* strip inside strings; dbt SQL
rarely has Jinja inside string literals.)

### 4.2 Target env

**Primary env signal: env-tags in `tags=[...]`.**

The repo convention encodes deployment envs as tags of the shape
`<region>_<env>`, e.g. `us_prd`, `eu_prd`, `us_dev`, `eu_dev`. A single
model commonly carries *multiple* env-tags because it is deployed to
multiple envs.

Two sources, in order of precedence:

1. **In-file `{{ config(...) }}`** - parse with a tolerant regex that
   captures the *body* of the first `config(...)` call, then a key=value
   extractor for `alias`, `tags`, `catalog`, `database`, `schema`,
   `databricks_tags`.
   - `databricks_tags` is a metadata dict (owner, layer, sensitivity, …)
     and is **not** used for env resolution; only `tags` is.
2. **Sibling `.yml`** - find the model's stanza in any `*.yml` in the same
   dir or any ancestor under the project root:
   ```yaml
   models:
     - name: foo
       config:
         tags: [us_prd, eu_prd]
       tags: [us_prd, eu_prd]   # either location accepted
   ```
   Merge with config-block; tags are union'd across both.

**Env-tag extraction**: from the merged tag list, keep tags matching:

```
^[a-z]{2,5}_(prd|prod|dev|stg|staging|uat|qa|test)$
```

(case-insensitive; everything normalized to lowercase). Tags like `gold`,
`marts`, `daily`, `client` are *not* env-tags and are ignored for env
purposes (but kept in `tags` for reporting).

Resolved env signal:
```json
{
  "catalog":  "..." | null,    // informational only
  "schema":   "..." | null,    // informational only
  "tags":     ["us_prd","eu_prd","gold"],
  "env_tags": ["us_prd","eu_prd"],
  "meta_env": "prod" | null    // legacy fallback, optional
}
```

A model with *no* env-tags at all → `WARN` (env can't be verified).

## 5. Reference resolution

`check_refs.py <parsed_json> <discovery_json>`:

### 5.1 `ref('name')`
- Build a map `model_name → model_path` from the discovery `models` list
  (`Path.stem`). Collisions (same stem in two dirs) are themselves a `WARN`.
- Lookup hit → `PASS` with resolved path.
- Miss → `FAIL`.

### 5.2 `source('src','tbl')`
- Parse each `sources.yml`, build:
  ```
  (source_name, table_name) → {
     "yaml_path": ...,
     "database":  ...,   # source-level or table-level override
     "schema":    ...,
     "tags":      [...],
     "meta":      {...}
  }
  ```
- Lookup hit → `PASS`. Miss → `FAIL`.

Output: a list of `{kind, raw, status, detail, resolved_env}` rows.

## 6. Column checks

`check_columns.py <target_sql> <parsed_json> <refs_json> <discovery_json>`:

This script performs two checks:
1. Verifies that qualified column references (`alias.column`) in the target
   SQL actually exist in the referenced model's or source's YAML column
   declarations.
2. Flags backtick-quoted column aliases containing spaces, which break dbt.

### 6.1 Alias-to-ref/source mapping

The script scans the comment-stripped SQL for `ref()`/`source()` Jinja
expressions followed by an optional alias:

- `{{ ref('stg_orders') }} AS orders` → alias `orders` maps to ref `stg_orders`
- `{{ ref('stg_orders') }} orders` → same (AS is optional)
- `{{ ref('stg_orders') }}` with no alias → alias defaults to `stg_orders`
- Same patterns for `source()`, defaulting to the table name

### 6.2 Qualified column extraction

A regex scans for `identifier.identifier` patterns, filtering out:
- SQL keywords on either side (e.g. `group.by` is not a column reference)
- Aliases that don't appear in the alias map (e.g. CTE names, function
  namespaces)
- Duplicate `(alias, column)` pairs (each pair reported only once)

### 6.3 Column declaration lookup

For each alias, the script resolves declared columns from:

- **Models** (`ref()`): Walk up from the resolved model's `.sql` file looking
  for `*.yml` with a `models:` stanza matching the model name, then read its
  `columns:` list.
- **Sources** (`source()`): Scan `source_yamls` from discovery for the matching
  `(source_name, table_name)` entry, then read its `columns:` list.

### 6.4 Verdict rules

| Declared columns in YAML? | Column found? | Result |
|---------------------------|---------------|--------|
| Yes (has `columns:` list) | Yes           | PASS   |
| Yes (has `columns:` list) | No            | FAIL   |
| No (`columns:` absent)    | n/a           | WARN   |

### 6.5 Alias naming check

The script also scans the comment-stripped SQL for backtick-quoted column
aliases containing whitespace:

```sql
-- These are flagged as FAIL:
institution_name  AS `Institution Name`,
contract_year     AS `Contract Year`,
```

The regex ``AS\s+`([^`]*\s[^`]*)```` (case-insensitive) catches these.
Each match emits a result with `kind: "alias_naming"` and `status: "FAIL"`.

dbt requires column aliases to be valid identifiers. Backtick-quoted names
with spaces may appear to work in some SQL engines but break dbt compilation,
downstream `ref()` resolution, and schema YAML `columns:` matching.

### 6.6 Scope limitations

- Only qualified `alias.column` references are checked. Bare `column_name`
  without a table prefix is skipped - the source table is ambiguous without
  full SQL semantic analysis.
- Column declarations must exist in YAML. The referenced model's SELECT clause
  is not parsed (that would require dbt compile or a SQL parser).
- CTE-defined columns are not tracked. Only columns from `ref()`/`source()`
  aliases are validated.

## 7. Env consistency

`check_env.py <parsed_json> <refs_json>`:

### 7.1 Subset rule (per-ref)

For each successfully-resolved ref/source, derive its `env_tags` the same
way as the target, then apply the **subset rule**:

> Every env-tag on the target must also be present on the referenced
> object. The ref may carry *extra* envs the target doesn't — that's fine
> (e.g. a raw source deployed everywhere). It must not be *missing* any
> env the target ships to.

| Target env_tags | Ref env_tags | Result | Detail |
|-----------------|--------------|--------|--------|
| `{us_prd, eu_prd}` | `{us_prd, eu_prd, us_dev, eu_dev}` | PASS | target ⊆ ref |
| `{us_prd, eu_prd}` | `{us_prd, eu_prd}` | PASS | exact match |
| `{us_prd, eu_prd}` | `{us_prd}` | FAIL | ref missing: `eu_prd` |
| `{us_prd, eu_prd}` | `{us_dev, eu_dev}` | FAIL | ref missing: `us_prd, eu_prd` |
| `{}` (no env-tags) | anything | WARN | target has no env-tags |
| anything | `{}` (no env-tags) | WARN | ref has no env-tags |
| `{}` | `{}` | WARN | neither side has env-tags |

Catalog / schema mismatches are reported as `WARN` only — informational,
not used to FAIL the check, since the env convention here is tag-driven.

### 7.2 Promotion opportunities (aggregate)

After per-ref checks, the script computes the **intersection** of env-tags
across all resolved refs/sources that have env-tags. This intersection
represents the set of envs where *every* dependency exists. If that set
contains envs the target does not deploy to, those are safe promotion
targets — the target could be deployed there without breaking any joins.

```
promotable_envs = intersection(all ref env_tags) - target_env_tags
```

| Target env_tags | Ref A env_tags | Ref B env_tags | Promotable | Detail |
|-----------------|----------------|----------------|------------|--------|
| `{us_prd}` | `{us_prd, eu_prd}` | `{us_prd, eu_prd, us_dev}` | `{eu_prd}` | both refs support `eu_prd` |
| `{us_prd}` | `{us_prd, eu_prd}` | `{us_prd}` | `{}` | ref B doesn't support `eu_prd` |
| `{us_prd, eu_prd}` | `{us_prd, eu_prd}` | `{us_prd, eu_prd}` | `{}` | target already covers all |

Emitted as a single `WARN` with `kind: "promotion"` when `promotable_envs`
is non-empty. Skipped entirely when:
- The target has no env-tags (already a separate WARN).
- No resolved refs have env-tags.

## 8. CLAUDE.md rule application

`CLAUDE.md` is prose. Mechanical scripting is the wrong tool. Workflow:

1. The agent reads `project_root/CLAUDE.md`.
2. It extracts a checklist of *checkable* rules (style, required headers,
   naming, forbidden patterns, required `config()` keys, etc.).
3. It applies each rule to the target SQL and records PASS/FAIL/WARN +
   evidence (line ref).

`SKILL.md` provides the agent a small **rule-extraction rubric** (see §10) so
this step is consistent.

## 9. Report format

Filled from `report-template.md`:

```markdown
# dbt SQL validation — <relative path>

**Verdict**: PASS / FAIL / WARN
**Project root**: <path>
**Target env**: catalog=<...> schema=<...> tags=[...]

## 1. CLAUDE.md compliance
- ✅ Rule: <text>  — <evidence>
- ❌ Rule: <text>  — line 42: <snippet>

## 2. Reference existence
- ✅ ref('stg_orders') → models/staging/stg_orders.sql
- ❌ source('raw','ordrs')  — not found in any sources.yml

## 3. Env consistency

### 3a. Subset rule
- ✅ ref('stg_orders'): target {us_prd, eu_prd} ⊆ ref {us_prd, eu_prd, us_dev, eu_dev}
- ❌ source('raw','events'): ref is missing env-tags {eu_prd} that target ships to
- ⚠ ref('dim_date'): no env-tags — cannot verify

### 3b. Promotion opportunities
- ⚠ all dependencies support {us_dev, eu_dev} — target could be promoted

## 4. Column checks

### 4a. Column existence
- ✅ ref('stg_orders').`customer_id` — declared in schema YAML
- ❌ ref('stg_orders').`cusotmer_id` — not found in declared columns [customer_id, order_id, ...]
- ⚠ ref('dim_products') — no columns declared in schema YAML, cannot verify

### 4b. Alias naming
- ❌ line 15: AS `Institution Name` — backtick-quoted alias with spaces

## Summary
| Section          | PASS | FAIL | WARN |
|------------------|------|------|------|
| CLAUDE.md        |  ... |  ... |  ... |
| References       |  ... |  ... |  ... |
| Env consistency  |  ... |  ... |  ... |
| Column checks    |  ... |  ... |  ... |
```

## 10. Rule-extraction rubric (for the agent step)

When the agent reads `CLAUDE.md`, it should:

1. Skip narrative/intro sections.
2. For every imperative sentence ("Always …", "Never …", "Models must …",
   "Use snake_case …"), turn it into one checklist item.
3. Classify each item:
   - **Mechanical** (can be confirmed by regex / file lookup) → run check
     directly on the SQL.
   - **Judgement** (requires reading intent) → still check, but mark
     `WARN` when uncertain rather than `FAIL`.
4. Never invent rules not present in `CLAUDE.md`.

## 11. Error handling

- Target file doesn't exist → exit non-zero with clear message.
- No `dbt_project.yml` found walking up → exit non-zero.
- `CLAUDE.md` missing → continue; report section 1 with single `WARN:
  CLAUDE.md not found`.
- Malformed YAML → that yaml is skipped, logged as a `WARN`.

## 12. Open design decisions deferred to v2

- Live Databricks `INFORMATION_SCHEMA` check (requires creds).
- `manifest.json` integration for cross-package refs.
- Auto-fix proposals (e.g. "did you mean `stg_orders` instead of
  `stg_order`?" via Levenshtein on the model name map).
- Multi-`CLAUDE.md` merging (root + per-domain).
- Unqualified column resolution using SQL semantic analysis or dbt compile.
- Parsing referenced models' SELECT clauses to infer output columns when
  schema YAML `columns:` is absent.
