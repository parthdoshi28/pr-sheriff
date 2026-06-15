# dbt Agent Skills

Three Cursor / Claude Code skills for the CNGBA dbt workflow — install individually or together from this repo.

| Skill | Directory | Purpose |
|-------|-----------|---------|
| **code-creator** | [`code-creator/`](code-creator/) | Generate a draft dbt model from a Jira ticket |
| **pr-sheriff** | [`pr-sheriff/`](pr-sheriff/) | Validate a dbt `.sql` model offline |
| **dbt-workflow** | [`dbt-workflow/`](dbt-workflow/) | Run code-creator then pr-sheriff in sequence |

## Prerequisites

Clone this repo once, then symlink (or copy) each skill you want into your skills directory. Restart Cursor or Claude Code after installing.

| Skill | Setup required? |
|-------|-----------------|
| **pr-sheriff** | Python 3.9+ and `pip install pyyaml` only — runs offline |
| **code-creator** | [SETUP.md](SETUP.md) — Jira, Databricks, GitHub credentials |
| **dbt-workflow** | Same as code-creator |

**First time using code-creator or dbt-workflow:**

```bash
python code-creator/scripts/setup_credentials.py
python code-creator/scripts/check_setup.py
```

### Cursor

```powershell
# Clone the repo
git clone https://github.com/parthdoshi28/pr-sheriff.git "$env:USERPROFILE\.cursor\skills\dbt-agent-skills"

# Symlink individual skills (pick any or all three)
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.cursor\skills\code-creator" -Target "$env:USERPROFILE\.cursor\skills\dbt-agent-skills\code-creator"
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.cursor\skills\pr-sheriff" -Target "$env:USERPROFILE\.cursor\skills\dbt-agent-skills\pr-sheriff"
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.cursor\skills\dbt-workflow" -Target "$env:USERPROFILE\.cursor\skills\dbt-agent-skills\dbt-workflow"
```

Mac / Linux — replace `$env:USERPROFILE\.cursor\skills` with `~/.cursor/skills` and use `ln -s` instead of `New-Item`.

### Claude Code

Same pattern under `~/.claude/skills/` (or `%USERPROFILE%\.claude\skills\` on Windows).

### Without symlinks

Reference a skill file directly in chat, e.g. `@dbt-agent-skills/pr-sheriff/SKILL.md`.

## Use

**Create a draft model from a ticket**

```
Use code-creator for CNGBA-123
```

**Validate an existing model**

```
Use pr-sheriff to validate models/marts/my_model.sql
```

**Full pipeline (create + validate)**

```
Use dbt-workflow for CNGBA-123
```

When using **code-creator** or **pr-sheriff** alone, review that skill's output before starting another step. **dbt-workflow** pauses between phases unless the user already asked to run both.

## Repository layout

```
dbt-agent-skills/          ← clone target (repo name: pr-sheriff)
├── README.md              ← this file
├── SETUP.md               ← credential setup (code-creator / dbt-workflow)
├── code-creator/
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── setup_credentials.py
│   │   └── check_setup.py
│   └── README.md
├── pr-sheriff/
│   ├── SKILL.md
│   ├── scripts/
│   ├── report-template.md
│   └── SETUP.md           ← optional Databricks MCP for pr-sheriff
└── dbt-workflow/
    └── SKILL.md           ← wrapper orchestrating the other two
```

## Child skill docs

- [SETUP.md](SETUP.md) — credential wizard for code-creator and dbt-workflow
- [code-creator README](code-creator/README.md) — Jira + Databricks + GitHub details
- [pr-sheriff SKILL.md](pr-sheriff/SKILL.md) — offline validation workflow
- [pr-sheriff SETUP.md](pr-sheriff/SETUP.md) — optional Databricks MCP for ref fallback
