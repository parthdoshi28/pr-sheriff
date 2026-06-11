#!/usr/bin/env python3
"""
get_schema.py — Fetch column metadata from Unity Catalog dev; fall back to dbt model SQL.

Usage: python get_schema.py catalog.schema.table [catalog.schema.table ...]
Output: JSON to stdout — keyed by table reference, with columns list and source tag.
"""

import json
import os
import re
import sys
from pathlib import Path


def load_env():
    vars_needed = ["DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_ACCESS_TOKEN"]
    env = {}
    missing = []
    for var in vars_needed:
        val = os.environ.get(var)
        if not val:
            missing.append(var)
        env[var] = val
    if missing:
        for var in missing:
            print(f"Missing required environment variable: {var}", file=sys.stderr)
        sys.exit(1)
    return env


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
        {
            "name": row[0],
            "type": row[1],
            "description": row[2] or "",
            "source": "unity_catalog",
        }
        for row in rows
    ]


def find_dbt_model_sql(repo_root, table_name):
    """Search kyc_dbt_platform/models/ for a .sql file whose stem contains table_name."""
    models_dir = Path(repo_root) / "kyc_dbt_platform" / "models"
    if not models_dir.exists():
        return None
    for sql_file in models_dir.rglob("*.sql"):
        if table_name in sql_file.stem:
            return sql_file
    return None


def infer_columns_from_sql(sql_file):
    """Parse the final SELECT in a dbt SQL file to infer column names (types = 'unknown')."""
    try:
        sql = sql_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Warning: could not read {sql_file}: {exc}", file=sys.stderr)
        return []

    # Neutralise Jinja so regex doesn't trip on {{ ... }}
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
        if alias_match:
            col_name = alias_match.group(1)
        else:
            col_name = raw.split()[-1].strip("`\"'")
        if col_name and col_name.upper() not in ("SELECT", "__JINJA__"):
            columns.append(
                {
                    "name": col_name,
                    "type": "unknown",
                    "description": "",
                    "source": "dbt_model_inferred",
                }
            )
    return columns


def main():
    if len(sys.argv) < 2:
        print("Usage: python get_schema.py catalog.schema.table [...]", file=sys.stderr)
        print("Example: python get_schema.py kyc_aws_us_dev_silver_catalog.kyc.my_table", file=sys.stderr)
        sys.exit(1)

    env = load_env()
    table_refs = sys.argv[1:]

    # scripts/ lives one level below repo root
    repo_root = Path(__file__).resolve().parent.parent

    try:
        from databricks import sql as dbsql
    except ImportError:
        print(
            "databricks-sql-connector not installed. Run: pip install databricks-sql-connector",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        connection = dbsql.connect(
            server_hostname=env["DATABRICKS_HOST"].replace("https://", ""),
            http_path=env["DATABRICKS_HTTP_PATH"],
            access_token=env["DATABRICKS_ACCESS_TOKEN"],
        )
        cursor = connection.cursor()
    except Exception as exc:
        print(f"Databricks connection failed: {exc}", file=sys.stderr)
        sys.exit(1)

    results = {}

    for table_ref in table_refs:
        parts = table_ref.strip().split(".")
        if len(parts) != 3:
            print(
                f"Warning: '{table_ref}' is not catalog.schema.table format — skipping",
                file=sys.stderr,
            )
            results[table_ref] = {"columns": [], "source": "invalid_format"}
            continue

        catalog, schema, table = parts
        columns = get_columns_from_uc(cursor, catalog, schema, table)

        if columns:
            results[table_ref] = {"columns": columns, "source": "unity_catalog"}
        else:
            sql_file = find_dbt_model_sql(repo_root, table)
            if sql_file:
                print(
                    f"Warning: '{table_ref}' not found in Unity Catalog dev — "
                    f"falling back to dbt model '{sql_file.name}' (types will be 'unknown')",
                    file=sys.stderr,
                )
                columns = infer_columns_from_sql(sql_file)
                results[table_ref] = {
                    "columns": columns,
                    "source": "dbt_model_inferred",
                    "dbt_model_file": str(sql_file.relative_to(repo_root)),
                }
            else:
                results[table_ref] = {"columns": [], "source": "not_found"}

    cursor.close()
    connection.close()

    print(json.dumps(results, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
