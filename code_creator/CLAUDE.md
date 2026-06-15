# CLAUDE.md — Code Creator Agent

This file governs how Claude Code behaves when working in this directory.
Read it before making any changes to scripts, the slash command, or design/plan docs.

---

## Before implementing anything — including patch fixes

**Always do this first, without exception:**

1. Read `design.md` — understand what the agent does and why
2. Read `plan.md` — understand the acceptance criteria for the affected component
3. Identify *why* the gap or bug exists: is it a missing requirement, a wrong assumption, a responsibility in the wrong place?
4. Update the acceptance criteria in `plan.md` (and design decisions in `design.md` if the approach changes) to reflect the correct behaviour
5. Only then write or change code

This applies to patch fixes too. A patch that papers over a symptom without understanding the root cause will break again. If you cannot explain why the problem exists in terms of `design.md` or `plan.md`, you do not yet understand it well enough to fix it.

---

## Responsibility split: Claude vs. scripts

This is the most important architectural rule.

| Responsibility | Owner |
|---|---|
| Read and understand ticket description | Claude (slash command) |
| Extract business logic, aggregations, filters, GROUP BY | Claude (slash command) |
| Generate the dbt SQL body (CTEs, SELECT columns, WHERE, GROUP BY) | Claude (slash command) |
| Wrap SQL in `{{ config() }}` block with correct tags and meta | `create_branch.py` |
| Infer domain, layer, model name | `create_branch.py` |
| Create GitHub branch from dev HEAD | `create_branch.py` |
| Commit SQL + schema.yml to the branch | `create_branch.py` |
| Query Unity Catalog for column metadata | `get_schema.py` |
| Fetch dbt SQL from GitHub when UC is unavailable | `get_schema.py` |
| Post Jira comment | Atlassian MCP in slash command |

**`create_branch.py` must never generate business logic.** It handles scaffolding and GitHub mechanics only. The `generate_sql()` function exists as a last-resort fallback (CI/CD path with no LLM) — it must never be the primary path in the interactive slash command.

---

## SQL generation requirements

When Claude generates the dbt SQL body in the slash command:

1. **Read the ticket description fully.** Extract:
   - Explicit SQL snippets the author included — use these as the primary logic hint
   - Named metrics (e.g. "completed requests", "failure rate", "broken down by MM/YY")
   - Filter conditions mentioned in plain English
   - Aggregation intent (COUNT, SUM, ratio calculations)

2. **Never produce a flat column dump.** A gold model that just `SELECT col1, col2, ... FROM source` with no transformations is not a gold model — it is a copy. Gold models aggregate, filter, or derive metrics.

3. **Honour the ticket's source tables.** Use `{{ ref('model_name') }}` for internal dbt models, `{{ source('system', 'table') }}` for raw sources. Never hardcode catalog/schema names.

4. **Columns must be explicit.** No `SELECT *`, not even inside CTEs where avoidable.

5. **If the ticket's intent is unclear**, ask the user one clarifying question before generating. Do not guess and generate a wrong model silently.

---

## Definition of "done" for generated SQL

A generated SQL file is acceptable for commit only if:
- The `{{ config() }}` block has all 8 required meta keys (empty string is fine for unknowns)
- The model contains at least one transformation beyond a column rename (aggregation, filter, derived column, or business logic join)
- All column references are explicit
- `{{ ref() }}` / `{{ source() }}` are used — no hardcoded table paths
- The schema.yml entry has `unique` + `not_null` on the surrogate key and a `description` on every column
