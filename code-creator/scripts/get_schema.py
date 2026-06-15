#!/usr/bin/env python3
"""
get_schema.py — Fetch column metadata from Unity Catalog dev; fall back to dbt model SQL on GitHub.

Usage: python get_schema.py catalog.schema.table [catalog.schema.table ...]
Output: JSON to stdout — keyed by table reference, with columns list and source tag.

Fallback: when a table is absent from Unity Catalog dev, the script fetches the
kyc_dbt_platform/models/ file tree from GitHub (branch = GITHUB_BASE_BRANCH, default "dev"),
finds a matching .sql file by name, downloads it, and infers columns from the final SELECT.
No local clone of kyc-data-pipelines is required.
"""

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from _env import load_dotenv_file, require_vars

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass


def load_env():
    env = require_vars([
        "DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_ACCESS_TOKEN",
        "GITHUB_TOKEN", "GITHUB_REPO",
    ])
    env["GITHUB_BASE_BRANCH"] = os.environ.get("GITHUB_BASE_BRANCH", "dev")
    return env


# ──────────────────────────────────────────────
# Unity Catalog query
# ──────────────────────────────────────────────

def get_columns_from_uc(cursor, catalog, schema, table):
    """Query information_schema.columns. Returns list of column dicts or None if absent."""
    try:
        cursor.execute(
            f"""
            SELECT column_name, data_type, comment
            FROM `{catalog}`.information_schema.columns
            WHERE table_schema = '{schema}'
              AND table_name   = '{table}'
            ORDER BY ordinal_position
            """
        )
        rows = cursor.fetchall()
    except Exception as exc:
        print(f"Warning: UC query failed for {catalog}.{schema}.{table}: {exc}", file=sys.stderr)
        return None
    if not rows:
        return None
    return [
        {"name": row[0], "type": row[1], "description": row[2] or "", "source": "unity_catalog"}
        for row in rows
    ]


# ──────────────────────────────────────────────
# GitHub helpers
# ──────────────────────────────────────────────

def github_get(token, path):
    """GET from GitHub API. Returns parsed JSON or None on 404."""
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"GitHub API error (HTTP {e.code}) GET {url}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Network error reaching GitHub: {e.reason}", file=sys.stderr)
        sys.exit(1)


def fetch_model_sql_paths(token, repo, branch):
    """Return list of paths to .sql files under kyc_dbt_platform/models/ on the branch."""
    data = github_get(token, f"/repos/{repo}/git/trees/{branch}?recursive=1")
    if not data:
        print(f"Warning: could not fetch repo tree from GitHub (branch={branch})", file=sys.stderr)
        return []
    return [
        item["path"]
        for item in data.get("tree", [])
        if item["type"] == "blob"
        and item["path"].startswith("kyc_dbt_platform/models/")
        and item["path"].endswith(".sql")
    ]


def find_model_path(sql_paths, table_name):
    """Return the first path whose filename stem contains table_name, or None."""
    for path in sql_paths:
        if table_name in Path(path).stem:
            return path
    return None


def fetch_file_text(token, repo, path, branch):
    """Download a file from GitHub and return its text content, or None."""
    data = github_get(token, f"/repos/{repo}/contents/{path}?ref={branch}")
    if not data:
        return None
    try:
        return base64.b64decode(data["content"]).decode("utf-8")
    except Exception as exc:
        print(f"Warning: could not decode {path}: {exc}", file=sys.stderr)
        return None


# ──────────────────────────────────────────────
# dbt SQL column inference
# ──────────────────────────────────────────────

def infer_columns_from_sql(sql):
    """Parse the final SELECT in a dbt SQL string. Returns list of column dicts."""
    sql_clean = re.sub(r"\{\{.*?\}\}", "__JINJA__", sql, flags=re.DOTALL)
    sql_clean = re.sub(r"\{%-?.*?-%?\}", "", sql_clean, flags=re.DOTALL)

    upper = sql_clean.upper()
    last_select = upper.rfind("SELECT")
    if last_select == -1:
        return []

    after_select = sql_clean[last_select + 6:]
    from_idx = after_select.upper().find("\nFROM")
    if from_idx == -1:
        from_idx = after_select.upper().find(" FROM")
    if from_idx == -1:
        return []

    column_block = after_select[:from_idx].strip()
    columns = []
    for raw in column_block.split(","):
        raw = raw.strip()
        if not raw:
            continue
        alias_match = re.search(r"\bAS\s+(\w+)\s*$", raw, re.IGNORECASE)
        col_name = alias_match.group(1) if alias_match else raw.split()[-1].strip("`\"'")
        if col_name and col_name.upper() not in ("SELECT", "__JINJA__"):
            columns.append({
                "name": col_name,
                "type": "unknown",
                "description": "",
                "source": "dbt_model_inferred",
            })
    return columns


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python get_schema.py catalog.schema.table [...]", file=sys.stderr)
        print("Example: python get_schema.py kyc_aws_us_dev_silver_catalog.kyc.my_table", file=sys.stderr)
        sys.exit(1)

    env = load_env()
    table_refs = sys.argv[1:]
    token = env["GITHUB_TOKEN"]
    repo = env["GITHUB_REPO"]
    branch = env["GITHUB_BASE_BRANCH"]

    try:
        from databricks import sql as dbsql
    except ImportError:
        print(
            "databricks-sql-connector not installed. Run: pip install databricks-sql-connector",
            file=sys.stderr,
        )
        sys.exit(1)

    cursor = None
    connection = None
    try:
        connection = dbsql.connect(
            server_hostname=env["DATABRICKS_HOST"].replace("https://", ""),
            http_path=env["DATABRICKS_HTTP_PATH"],
            access_token=env["DATABRICKS_ACCESS_TOKEN"],
        )
        cursor = connection.cursor()
    except Exception as exc:
        print(f"Warning: Databricks connection failed ({exc}) — skipping UC queries, using GitHub fallback only.", file=sys.stderr)

    # Lazy-load GitHub model file list (once, shared across all fallback lookups)
    sql_paths_cache = None

    results = {}

    for table_ref in table_refs:
        parts = table_ref.strip().split(".")
        if len(parts) != 3:
            print(f"Warning: '{table_ref}' is not catalog.schema.table format — skipping", file=sys.stderr)
            results[table_ref] = {"columns": [], "source": "invalid_format"}
            continue

        catalog, schema, table = parts
        columns = get_columns_from_uc(cursor, catalog, schema, table) if cursor else None

        if columns:
            results[table_ref] = {"columns": columns, "source": "unity_catalog"}
        else:
            if sql_paths_cache is None:
                print("Table absent from UC dev — fetching dbt model list from GitHub...", file=sys.stderr)
                sql_paths_cache = fetch_model_sql_paths(token, repo, branch)

            model_path = find_model_path(sql_paths_cache, table)
            if model_path:
                print(
                    f"Warning: '{table_ref}' not in UC dev — "
                    f"falling back to '{Path(model_path).name}' from GitHub (types: unknown)",
                    file=sys.stderr,
                )
                sql_text = fetch_file_text(token, repo, model_path, branch)
                if sql_text:
                    results[table_ref] = {
                        "columns": infer_columns_from_sql(sql_text),
                        "source": "dbt_model_inferred",
                        "dbt_model_file": model_path,
                    }
                else:
                    results[table_ref] = {"columns": [], "source": "not_found"}
            else:
                results[table_ref] = {"columns": [], "source": "not_found"}

    if cursor:
        cursor.close()
    if connection:
        connection.close()

    print(json.dumps(results, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
