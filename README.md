# PR Sheriff

Cursor Agent Skill that validates a single dbt `.sql` model offline (no warehouse connection):

- **CLAUDE.md compliance** — project root rules applied to the SQL
- **Reference existence** — every `ref()` / `source()` resolves to a real model or `sources.yml` entry
- **Env consistency** — deployment env-tags on the target are a subset of each dependency's env-tags
- **Column checks** — qualified column references match declared schema/source YAML columns

Databricks-flavored. File-scan only — no `dbt compile`, no `manifest.json` required.

## Install

Clone into your personal Cursor skills directory:

```bash
git clone https://github.com/parthdoshi28/pr-sheriff.git ~/.cursor/skills/pr-sheriff
```

Or on Windows (PowerShell):

```powershell
git clone https://github.com/parthdoshi28/pr-sheriff.git "$env:USERPROFILE\.cursor\skills\pr-sheriff"
```

**Prerequisites:** Python 3.9+ and `pyyaml` (`pip install pyyaml`).

## Use

In Cursor, invoke the skill explicitly and provide a dbt model path:

```
Use pr-sheriff to validate models/marts/my_model.sql
```

See [SKILL.md](SKILL.md) for the full workflow, scripts, and report format.

## Scripts

Run checks manually from the skill directory:

```bash
python scripts/discover_project.py path/to/model.sql
python scripts/parse_target.py path/to/model.sql <project_root>
python scripts/check_refs.py parsed.json discovery.json
python scripts/check_columns.py path/to/model.sql parsed.json refs.json discovery.json
python scripts/check_env.py parsed.json refs.json
```

Or run all checks in one pass:

```bash
python scripts/_run_all.py path/to/model.sql
```
