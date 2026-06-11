#!/usr/bin/env python3
"""
create_branch.py — Generate draft dbt SQL + schema.yml, push to a feature branch, open a Draft PR.

Usage:
    python create_branch.py --ticket ticket.json --schema schema.json
    python create_branch.py --ticket ticket.json          # schema defaults to {}
    echo '{"ticket":{...},"schema":{...}}' | python create_branch.py
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


# ──────────────────────────────────────────────
# Domain / layer inference
# ──────────────────────────────────────────────

DOMAINS = [
    "client_usage", "eecr", "eva", "grid", "maxsight",
    "ml_logging", "npi", "orbis", "quantexa", "screening",
]

LAYER_KEYWORDS = {
    "bronze": ["bronze", "raw", "ingest", "landing"],
    "silver": ["silver", "cleanse", "clean", "transform", "staging", "intermediate"],
    "gold":   ["gold", "mart", "reporting", "aggregate", "agg", "final"],
}

STOP_WORDS = {
    "add", "create", "build", "new", "update", "model", "dbt",
    "table", "view", "for", "the", "a", "an", "and", "or",
}


def infer_domain(text):
    text_lower = text.lower()
    return [d for d in DOMAINS if d in text_lower]


def infer_layer(text):
    text_lower = text.lower()
    for layer, keywords in LAYER_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return layer
    print("Assuming layer = 'silver' (no explicit layer keyword found in ticket)", file=sys.stderr)
    return "silver"


def extract_descriptor(summary, domain, layer):
    text = re.sub(r"CNGBA-\d+", "", summary, flags=re.IGNORECASE)
    text = re.sub(r"\b(" + re.escape(domain) + r"|" + re.escape(layer) + r")\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^a-zA-Z0-9\s]", " ", text)
    words = [w.lower() for w in text.split() if w and w.lower() not in STOP_WORDS]
    return "_".join(words[:5]) or "model"


# ──────────────────────────────────────────────
# Code generation
# ──────────────────────────────────────────────

def generate_sql(ticket, schema, domain, layer, model_name):
    assignee = ticket.get("assignee", "unknown")
    ticket_id = ticket.get("ticket_id", "CNGBA-000")
    summary = ticket.get("summary", "")[:80]

    # Collect columns, deduped
    all_columns = []
    seen_cols = set()
    for table_info in schema.values():
        for col in table_info.get("columns", []):
            if col["name"] not in seen_cols:
                all_columns.append(col["name"])
                seen_cols.add(col["name"])

    if not all_columns:
        all_columns = ["-- TODO: list columns explicitly"]

    # Build CTEs
    cte_blocks = []
    for table_ref, table_info in schema.items():
        parts = table_ref.split(".")
        alias = parts[-1] if parts else "source_table"
        if "bronze" in table_ref.lower() or table_info.get("source") == "unity_catalog":
            source_system = parts[1] if len(parts) >= 2 else alias
            cte_blocks.append(
                f"    {alias} as (\n"
                f"        select * from {{{{ source('{source_system}', '{parts[-1]}') }}}}\n"
                f"    )"
            )
        else:
            cte_blocks.append(
                f"    {alias} as (\n"
                f"        select * from {{{{ ref('{parts[-1]}') }}}}\n"
                f"    )"
            )

    primary_alias = list(schema.keys())[0].split(".")[-1] if schema else "source_table"

    if not cte_blocks:
        cte_blocks = [
            f"    {primary_alias} as (\n"
            f"        select * from {{{{ source('{primary_alias}', '{primary_alias}') }}}}\n"
            f"    )"
        ]

    pk_col = all_columns[0] if all_columns and not all_columns[0].startswith("--") else "id"
    col_list = ",\n    ".join(all_columns)

    return (
        f"{{{{ config(\n"
        f"    tags=[\"{domain}_prd\", \"{domain}_stg\", \"{domain}_dev\"],\n"
        f"    meta={{\n"
        f"        \"originator\": \"{assignee}\",\n"
        f"        \"maintainer\": \"{assignee}\",\n"
        f"        \"layer\": \"{layer}\",\n"
        f"        \"sensitivity\": \"\",\n"
        f"        \"compliance\": \"\",\n"
        f"        \"use_case\": \"{summary}\",\n"
        f"        \"refresh_freq_and_ingest\": \"\",\n"
        f"        \"consumption\": \"\"\n"
        f"    }}\n"
        f") }}}}\n\n"
        f"-- {ticket_id}: {ticket.get('summary', '')}\n"
        f"with\n\n"
        + ",\n\n".join(cte_blocks)
        + f"\n\nselect\n"
        f"    MD5(CONCAT_WS('||', {pk_col})) as {model_name}_key,\n"
        f"    {col_list}\n"
        f"from {primary_alias}\n"
    )


def generate_schema_entry(model_name, ticket, schema):
    """Return a YAML block (string) to be appended to an existing schema.yml models: list."""
    summary = ticket.get("summary", "")
    ticket_id = ticket.get("ticket_id", "CNGBA-000")

    lines = [
        f"  - name: {model_name}",
        f"    description: >",
        f"      {summary} ({ticket_id})",
        f"    columns:",
        f"      - name: {model_name}_key",
        f"        description: Surrogate key (MD5 hash)",
        f"        data_tests:",
        f"          - unique",
        f"          - not_null",
    ]

    seen = set()
    for table_info in schema.values():
        for col in table_info.get("columns", []):
            if col["name"] in seen:
                continue
            seen.add(col["name"])
            desc = col.get("description") or ""
            if col.get("source") == "dbt_model_inferred":
                desc = desc or "(inferred from dbt model — verify type and description)"
            lines.append(f"      - name: {col['name']}")
            lines.append(f"        description: {desc or 'TODO: add description'}")

    return "\n".join(lines) + "\n"


# ──────────────────────────────────────────────
# GitHub helpers
# ──────────────────────────────────────────────

def gh(token, method, path, body=None):
    """Authenticated GitHub API request. Exits on HTTP error."""
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        print(f"GitHub API error (HTTP {e.code}) {method} {url}: {body_text}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Network error reaching GitHub: {e.reason}", file=sys.stderr)
        sys.exit(1)


def fetch_file(token, repo, branch, path):
    """Return (content_str, sha) for a file on a branch, or ('', None) if not found."""
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
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
            data = json.loads(resp.read().decode())
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "", None
        print(f"GitHub API error reading {path} (HTTP {e.code})", file=sys.stderr)
        sys.exit(1)


def put_file(token, repo, branch, path, content, message, sha=None):
    """Create or update a file on a branch via the Contents API."""
    body = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    return gh(token, "PUT", f"/repos/{repo}/contents/{path}", body)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def load_env():
    env = {}
    missing = []
    for var in ["GITHUB_TOKEN", "GITHUB_REPO"]:
        val = os.environ.get(var)
        if not val:
            missing.append(var)
        env[var] = val
    if missing:
        for var in missing:
            print(f"Missing required environment variable: {var}", file=sys.stderr)
        sys.exit(1)
    env["GITHUB_BASE_BRANCH"] = os.environ.get("GITHUB_BASE_BRANCH", "dev")
    return env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticket", help="Path to ticket JSON file")
    parser.add_argument("--schema", help="Path to schema JSON file")
    args = parser.parse_args()

    # Load ticket
    if args.ticket:
        with open(args.ticket) as f:
            ticket = json.load(f)
        schema = {}
        if args.schema:
            with open(args.schema) as f:
                schema = json.load(f)
    elif not sys.stdin.isatty():
        stdin_data = json.load(sys.stdin)
        if "ticket" in stdin_data:
            ticket = stdin_data["ticket"]
            schema = stdin_data.get("schema", {})
        else:
            ticket = stdin_data
            schema = {}
    else:
        print("Provide ticket data via --ticket or stdin.", file=sys.stderr)
        sys.exit(1)

    env = load_env()
    token = env["GITHUB_TOKEN"]
    repo = env["GITHUB_REPO"]
    base_branch = env["GITHUB_BASE_BRANCH"]

    ticket_id = ticket["ticket_id"]
    ticket_text = ticket.get("summary", "") + " " + ticket.get("description", "")

    # Infer domain
    matched = infer_domain(ticket_text)
    if not matched:
        print(f"Could not infer domain. Known: {', '.join(DOMAINS)}", file=sys.stderr)
        domain = input("Enter domain: ").strip().lower()
    elif len(matched) == 1:
        domain = matched[0]
        print(f"Domain inferred: {domain}", file=sys.stderr)
    else:
        print(f"Ambiguous domain match: {matched}", file=sys.stderr)
        domain = input(f"Multiple domains found {matched}. Enter one: ").strip().lower()

    if domain not in DOMAINS:
        print(f"Unknown domain '{domain}'", file=sys.stderr)
        sys.exit(1)

    layer = infer_layer(ticket_text)
    descriptor = extract_descriptor(ticket.get("summary", ""), domain, layer)
    model_name = f"{domain}_{layer}_{descriptor}"
    sql_path = f"kyc_dbt_platform/models/{domain}/{layer}/{model_name}.sql"
    schema_path = f"kyc_dbt_platform/models/{domain}/{layer}/schema.yml"

    print(f"Model:  {model_name}", file=sys.stderr)
    print(f"SQL:    {sql_path}", file=sys.stderr)
    print(f"Schema: {schema_path}", file=sys.stderr)

    sql_content = generate_sql(ticket, schema, domain, layer, model_name)
    schema_entry = generate_schema_entry(model_name, ticket, schema)

    # Create branch from dev HEAD
    dev_ref = gh(token, "GET", f"/repos/{repo}/git/ref/heads/{base_branch}")
    base_sha = dev_ref["object"]["sha"]
    branch_name = f"feature/{ticket_id}"
    gh(token, "POST", f"/repos/{repo}/git/refs", {
        "ref": f"refs/heads/{branch_name}",
        "sha": base_sha,
    })
    print(f"Created branch: {branch_name}", file=sys.stderr)

    # Commit SQL file (new file — no existing SHA)
    put_file(token, repo, branch_name, sql_path, sql_content,
             f"{ticket_id}: add draft dbt model {model_name}")

    # Fetch existing schema.yml (or seed an empty one) then append
    existing, sha = fetch_file(token, repo, branch_name, schema_path)
    if not existing:
        existing = "version: 2\n\nmodels:\n"
    updated_schema = existing.rstrip() + "\n\n" + schema_entry
    put_file(token, repo, branch_name, schema_path, updated_schema,
             f"{ticket_id}: add schema.yml entry for {model_name}",
             sha=sha)

    # Open Draft PR
    pr = gh(token, "POST", f"/repos/{repo}/pulls", {
        "title": f"{ticket_id}: {ticket['summary']}",
        "body": (
            f"Jira: {ticket.get('jira_url', '')}\n\n"
            f"{ticket.get('summary', '')}\n\n"
            f"*Draft model generated by Code Creator agent.*"
        ),
        "head": branch_name,
        "base": base_branch,
        "draft": True,
    })

    print(pr["html_url"])
    sys.exit(0)


if __name__ == "__main__":
    main()
