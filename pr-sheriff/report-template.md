# dbt SQL validation — <relative_path>

**Verdict**: <PASS | FAIL | WARN>
**Project root**: <project_root>
**Target env-tags**: [<us_prd, eu_prd, ...>]
**Other tags**: [<gold, marts, ...>]
**Catalog/Schema**: catalog=<catalog_or_unset> schema=<schema_or_unset>

## 1. CLAUDE.md compliance

<one line per rule>
- ✅ Rule: <rule text>  — <evidence or "n/a">
- ❌ Rule: <rule text>  — line <N>: `<offending snippet>`
- ⚠ Rule: <rule text>  — <why uncertain>

> If CLAUDE.md is missing, replace this section with:
> ⚠ CLAUDE.md not found at `<project_root>/CLAUDE.md` — section skipped.

## 2. Reference existence

- ✅ ref('<name>') → `<resolved_path>`
- ❌ source('<src>', '<tbl>')  — not found in any sources.yml
- ⚠ ref('<name>')  — multiple matches: <paths>
- ⚠ ref('<name>')  — not in repo files, found in Databricks at `<catalog.schema.table_name>` (MCP fallback)
- ❌ ref('<name>')  — not found in repo files or Databricks catalog

## 3. Env consistency (env-tags)

### 3a. Subset rule

- ✅ ref('<name>'): target {us_prd, eu_prd} ⊆ ref {us_prd, eu_prd, us_dev, eu_dev}
- ❌ source('<src>','<tbl>'): ref is missing env-tags {eu_prd} that target ships to
- ⚠ ref('<name>'): ref has no env-tags — cannot verify
- ⚠ ref('<name>'): catalog differs (target=`prd_catalog` vs ref=`raw_catalog`) — informational

### 3b. Promotion opportunities

- ⚠ all dependencies support {eu_dev, eu_prd} that the target does not deploy to — target could be promoted

## 4. Column checks

### 4a. Column existence

- ✅ ref('<name>').`<column>` — declared in schema YAML
- ❌ ref('<name>').`<column>` — not found in declared columns [<col1>, <col2>, ...]
- ⚠ ref('<name>') — no columns declared in schema YAML, cannot verify `<column>`
- ⚠ source('<src>','<tbl>') — no columns declared in source YAML, cannot verify `<column>`

### 4b. Alias naming

- ❌ line <N>: AS \`Institution Name\` — backtick-quoted alias with spaces; column aliases must be valid identifiers
- ❌ line <N>: AS \`Contract Year\` — backtick-quoted alias with spaces; column aliases must be valid identifiers

## Summary

| Section          | PASS | FAIL | WARN |
|------------------|------|------|------|
| CLAUDE.md        |  <n> |  <n> |  <n> |
| References       |  <n> |  <n> |  <n> |
| Env consistency  |  <n> |  <n> |  <n> |
| Column checks    |  <n> |  <n> |  <n> |
