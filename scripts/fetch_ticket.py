#!/usr/bin/env python3
"""
fetch_ticket.py — Fetch a CNGBA Jira ticket and decide whether to proceed.

Usage: python fetch_ticket.py CNGBA-123
Output: JSON to stdout if ticket passes all gates; exit 0.
        "Skipping: ..." message + exit 0 if a gate rejects the ticket.
        Error message + exit 1 on API or config failure.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request


def load_env():
    """Load required env vars; print missing ones and exit 1 if any are absent."""
    vars_needed = ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "GITHUB_TOKEN", "GITHUB_REPO"]
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


def jira_get(base_url, email, token, path):
    """Authenticated GET against Jira REST API v3. Returns parsed JSON."""
    url = f"{base_url.rstrip('/')}{path}"
    credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"Ticket not found (HTTP 404): {url}", file=sys.stderr)
        else:
            print(f"Jira API error (HTTP {e.code}): {url}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Network error reaching Jira: {e.reason}", file=sys.stderr)
        sys.exit(1)


def github_branch_exists(token, repo, branch_name):
    """Return True if a GitHub branch already exists; False on 404."""
    url = f"https://api.github.com/repos/{repo}/branches/{branch_name}"
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
            resp.read()
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        print(f"GitHub API error (HTTP {e.code}) checking branch '{branch_name}'", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Network error reaching GitHub: {e.reason}", file=sys.stderr)
        sys.exit(1)


def flatten_adf(node):
    """Recursively extract plain text from an Atlassian Document Format (ADF) tree."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = [flatten_adf(child) for child in node.get("content", [])]
        return " ".join(p for p in parts if p)
    if isinstance(node, list):
        return " ".join(flatten_adf(item) for item in node if item)
    return ""


def main():
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} CNGBA-123", file=sys.stderr)
        sys.exit(1)

    ticket_id = sys.argv[1].strip().upper()
    env = load_env()

    # Fetch ticket
    data = jira_get(
        env["JIRA_BASE_URL"],
        env["JIRA_EMAIL"],
        env["JIRA_API_TOKEN"],
        f"/rest/api/3/issue/{ticket_id}",
    )

    fields = data.get("fields", {})
    status = (fields.get("status") or {}).get("name", "")
    summary = fields.get("summary") or ""
    description_raw = fields.get("description") or ""
    description = flatten_adf(description_raw) if isinstance(description_raw, dict) else str(description_raw)
    assignee = ((fields.get("assignee") or {}).get("displayName")) or "Unassigned"

    # Gate 1 — status
    if status not in ("To Do", "In Progress"):
        print(f"Skipping: status is '{status}', not To Do/In Progress")
        sys.exit(0)

    # Gate 2 — dbt keyword
    if "dbt" not in (summary + " " + description).lower():
        print("Skipping: no dbt keyword found in ticket title or description")
        sys.exit(0)

    # Gate 3 — idempotency
    branch_name = f"feature/{ticket_id}"
    if github_branch_exists(env["GITHUB_TOKEN"], env["GITHUB_REPO"], branch_name):
        print(f"Skipping: already processed (branch '{branch_name}' already exists on GitHub)")
        sys.exit(0)

    # All gates passed
    result = {
        "ticket_id": ticket_id,
        "summary": summary,
        "description": description,
        "status": status,
        "assignee": assignee,
        "jira_url": f"{env['JIRA_BASE_URL'].rstrip('/')}/browse/{ticket_id}",
    }
    print(json.dumps(result, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
