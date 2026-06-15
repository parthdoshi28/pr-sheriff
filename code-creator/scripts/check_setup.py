#!/usr/bin/env python3
"""
check_setup.py — Verify code-creator / dbt-workflow credentials are configured.

Usage:
  python check_setup.py [--env-file PATH]

Stdout: JSON with status PASS|FAIL, missing vars, and setup hint.
Exit 0 when PASS, 1 when FAIL.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REQUIRED = [
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "DATABRICKS_HOST",
    "DATABRICKS_HTTP_PATH",
    "DATABRICKS_ACCESS_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_REPO",
]

OPTIONAL_WITH_DEFAULTS = {
    "GITHUB_BASE_BRANCH": "dev",
}


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def merged_env(env_file: pathlib.Path) -> dict[str, str]:
    import os

    merged = parse_env_file(env_file)
    for key in REQUIRED + list(OPTIONAL_WITH_DEFAULTS):
        if os.environ.get(key):
            merged[key] = os.environ[key]
    return merged


def check(env: dict[str, str]) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    placeholders: list[str] = []
    placeholder_markers = (
        "your-org.atlassian.net",
        "you@example.com",
        "your-workspace.azuredatabricks.net",
        "your-warehouse-id",
    )
    for key in REQUIRED:
        val = (env.get(key) or "").strip()
        if not val:
            missing.append(key)
            continue
        if any(marker in val for marker in placeholder_markers):
            placeholders.append(key)
    return missing, placeholders


def main() -> None:
    parser = argparse.ArgumentParser(description="Check code-creator credential setup.")
    parser.add_argument(
        "--env-file",
        default=str(pathlib.Path(__file__).resolve().parent.parent / ".env"),
        help="Path to .env file (default: code-creator/.env)",
    )
    args = parser.parse_args()
    env_file = pathlib.Path(args.env_file)
    env = merged_env(env_file)
    missing, placeholders = check(env)

    skill_dir = env_file.parent
    setup_cmd = f"python {skill_dir / 'scripts' / 'setup_credentials.py'}"

    if missing or placeholders:
        issues = missing + [f"{k} (placeholder value)" for k in placeholders]
        payload = {
            "status": "FAIL",
            "env_file": str(env_file),
            "missing": issues,
            "setup_command": setup_cmd,
            "message": (
                "Credential setup incomplete. Run setup_credentials.py before "
                "code-creator or dbt-workflow. pr-sheriff does not require this."
            ),
        }
        print(json.dumps(payload, indent=2))
        sys.exit(1)

    payload = {
        "status": "PASS",
        "env_file": str(env_file),
        "message": "All required credentials are configured.",
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
