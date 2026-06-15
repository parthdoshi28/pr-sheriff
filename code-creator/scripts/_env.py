"""Shared .env loading for code-creator scripts."""

from __future__ import annotations

import os
import pathlib
import sys

ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / ".env"

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


def load_dotenv_file() -> None:
    """Load code-creator/.env into os.environ (does not override existing vars)."""
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def require_vars(names: list[str]) -> dict[str, str]:
    load_dotenv_file()
    env: dict[str, str] = {}
    missing: list[str] = []
    for var in names:
        val = os.environ.get(var)
        if not val:
            missing.append(var)
        env[var] = val or ""
    if missing:
        for var in missing:
            print(f"Missing required environment variable: {var}", file=sys.stderr)
        print(
            f"Run: python {ENV_FILE.parent / 'scripts' / 'setup_credentials.py'}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return env
