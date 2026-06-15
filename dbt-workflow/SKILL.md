---
name: dbt-workflow
description: End-to-end dbt ticket workflow — generate a draft model from a CNGBA Jira ticket with code-creator, then validate it with pr-sheriff. Use when the user wants both creation and validation in one run, or asks to go from Jira ticket to reviewed dbt model. For a single step only, use code-creator or pr-sheriff directly.
disable-model-invocation: true
---

# dbt Workflow

Orchestrates two skills in sequence:

1. **code-creator** — draft dbt model from a Jira ticket
2. **pr-sheriff** — validate the generated SQL

Each child skill can also be invoked on its own. Use this wrapper only when the user wants the full pipeline.

## When to run

Required input:

- `TICKET_ID`: e.g. `CNGBA-123`

If the user only wants creation or only validation, stop and tell them to use **code-creator** or **pr-sheriff** instead.

**pr-sheriff runs offline** and does not need credential setup. This wrapper requires full setup before Phase 1.

## Step 0 — Verify credential setup (required)

Before Phase 1, run:

```bash
python <REPO_ROOT>/code-creator/scripts/check_setup.py
```

- If `"status": "PASS"` → continue.
- If `"status": "FAIL"` → **stop**. Direct the user to [SETUP.md](../SETUP.md) and:
  ```bash
  python <REPO_ROOT>/code-creator/scripts/setup_credentials.py
  ```

## Repository layout

This repo ships three sibling skill directories. Let `REPO_ROOT` be the directory that contains all three:

```
<REPO_ROOT>/
├── code-creator/SKILL.md
├── pr-sheriff/SKILL.md
└── dbt-workflow/SKILL.md   ← this file
```

When installed as personal skills, each directory is typically symlinked separately under `~/.cursor/skills/`. Resolve paths relative to wherever this skill's `SKILL.md` lives: sibling skills are at `<this-SKILL-dir>/../code-creator/` and `<this-SKILL-dir>/../pr-sheriff/`.

## Workflow

Copy this checklist and track progress:

```
- [ ] Step 0: Verify credential setup (check_setup.py)
- [ ] Phase 1: Code Creator (read code-creator/SKILL.md and execute)
- [ ] Checkpoint: confirm sql_path with user
- [ ] Phase 2: PR Sheriff (read pr-sheriff/SKILL.md and execute on sql_path)
- [ ] Final summary
```

### Phase 1 — Code Creator

1. Read `<REPO_ROOT>/code-creator/SKILL.md` in full.
2. Execute every step in that skill for the given `TICKET_ID`.
3. Capture from the Phase 1 summary:
   - `sql_path` — path to the generated `.sql` model
   - `schema_path` — path to the generated schema YAML
   - `branch` — feature branch name

If Phase 1 fails or exits early (e.g. ticket skipped), **stop**. Do not run pr-sheriff.

### Checkpoint — review before validation

Before Phase 2, show the user:

- Branch name and file paths from Phase 1
- Brief summary of generated business logic

Ask: **"Proceed with pr-sheriff validation on `<sql_path>`?"**

- If the user declines or wants edits first, stop after Phase 1.
- If the user confirms (or explicitly asked for the full workflow upfront), continue.

### Phase 2 — PR Sheriff

1. Read `<REPO_ROOT>/pr-sheriff/SKILL.md` in full.
2. Set `TARGET` to the `sql_path` from Phase 1.
3. Execute every step in that skill.
4. Render the pr-sheriff report inline in chat.

### Final summary

Combine both phases into one closing message:

- Ticket ID, branch, and file paths
- Code Creator outcome (draft created / skipped / failed)
- PR Sheriff overall verdict (`PASS` / `WARN` / `FAIL`)
- Next steps (fix FAILs, open PR, promote env tags, etc.)

## Using skills individually

| Goal | Skill to invoke |
|------|-----------------|
| Draft model from Jira ticket only | **code-creator** |
| Validate an existing `.sql` model | **pr-sheriff** |
| Ticket → draft → validate | **dbt-workflow** (this skill) |

When using a child skill alone, follow its `SKILL.md` end-to-end and review the output before starting anything else.
