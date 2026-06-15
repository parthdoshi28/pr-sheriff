#!/usr/bin/env python3
"""Locate the dbt project root above a target .sql file and inventory files.

Usage:
    python discover_project.py <target_sql>

Stdout: JSON
    {
      "project_root":  "/abs/path",
      "claude_md":     "/abs/path/CLAUDE.md" | null,
      "target":        "/abs/path/models/.../foo.sql",
      "models":        ["models/.../a.sql", ...],     # repo-relative
      "source_yamls":  ["models/.../sources.yml", ...],
      "schema_yamls":  ["models/.../_schema.yml", ...]
    }

Exits non-zero with a message on stderr if no dbt_project.yml is found.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def find_project_root(start: Path) -> Path | None:
    for parent in [start, *start.parents]:
        if (parent / "dbt_project.yml").is_file():
            return parent
    return None


def rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: discover_project.py <target_sql>", file=sys.stderr)
        return 2

    target = Path(sys.argv[1]).resolve()
    if not target.is_file():
        print(f"target not found: {target}", file=sys.stderr)
        return 2

    root = find_project_root(target.parent)
    if root is None:
        print(f"no dbt_project.yml found walking up from {target}", file=sys.stderr)
        return 2

    claude = root / "CLAUDE.md"
    claude_path = str(claude) if claude.is_file() else None

    models_dir = root / "models"
    models = (
        sorted(rel(p, root) for p in models_dir.rglob("*.sql"))
        if models_dir.is_dir()
        else []
    )

    source_yamls: list[str] = []
    schema_yamls: list[str] = []
    for yml in root.rglob("*.yml"):
        # skip dbt_project.yml itself and anything under target/, dbt_packages/, .venv/
        parts = set(yml.relative_to(root).parts)
        if {"target", "dbt_packages", ".venv", "venv"} & parts:
            continue
        name = yml.name.lower()
        if name == "sources.yml" or name.endswith("_sources.yml"):
            source_yamls.append(rel(yml, root))
        elif name == "schema.yml" or name.startswith("_") and name.endswith("schema.yml"):
            schema_yamls.append(rel(yml, root))
        else:
            # generic .yml inside models/ may also contain model stanzas
            if "models" in parts:
                schema_yamls.append(rel(yml, root))

    out = {
        "project_root": str(root),
        "claude_md": claude_path,
        "target": str(target),
        "models": models,
        "source_yamls": sorted(set(source_yamls)),
        "schema_yamls": sorted(set(schema_yamls)),
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
