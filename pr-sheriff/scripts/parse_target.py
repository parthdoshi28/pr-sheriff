#!/usr/bin/env python3
"""Parse refs, sources, and env signal from a dbt .sql target.

Usage:
    python parse_target.py <target_sql> <project_root>

Stdout: JSON
    {
      "target":  "<abs path>",
      "refs":    [{"name": "stg_orders", "line": 12, "raw": "{{ ref('stg_orders') }}"}],
      "sources": [{"source": "raw", "table": "orders", "line": 8, "raw": "..."}],
      "env": {
        "catalog":  "prod_analytics" | null,
        "schema":   "marts"          | null,
        "tags":     ["prod", ...],
        "meta_env": "prod"           | null,
        "from":     ["config_block", "sibling_yaml"]
      }
    }
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    print("pyyaml is required: pip install pyyaml", file=sys.stderr)
    sys.exit(3)


REF_RE = re.compile(
    r"""\{\{\s*ref\(\s*['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?\s*\)\s*\}\}""",
    re.VERBOSE,
)
SOURCE_RE = re.compile(
    r"""\{\{\s*source\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*\}\}""",
    re.VERBOSE,
)
CONFIG_RE = re.compile(r"\{\{\s*config\((.*?)\)\s*\}\}", re.DOTALL)
# key = 'string' | "string" | [list]  -- {dicts} are skipped (e.g. databricks_tags)
KV_RE = re.compile(
    r"""(\w+)\s*=\s*(?:
        '([^']*)' |
        "([^"]*)" |
        (\[[^\]]*\])
    )""",
    re.VERBOSE,
)
# env-tag pattern: <region>_<env>, e.g. us_prd, eu_prd, apac_dev
ENV_TAG_RE = re.compile(
    r"^[a-z]{2,5}_(prd|prod|dev|stg|staging|uat|qa|test)$",
    re.IGNORECASE,
)


def extract_env_tags(tags: list[str]) -> list[str]:
    seen: list[str] = []
    for t in tags or []:
        if isinstance(t, str) and ENV_TAG_RE.match(t):
            tl = t.lower()
            if tl not in seen:
                seen.append(tl)
    return seen


def strip_comments(sql: str) -> str:
    """Remove -- line, /* */ block, and {# #} Jinja comments. Naive but enough."""
    sql = re.sub(r"\{#.*?#\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def parse_config_block(raw_sql: str) -> dict:
    m = CONFIG_RE.search(raw_sql)
    if not m:
        return {}
    body = m.group(1)
    out: dict = {}
    for km in KV_RE.finditer(body):
        key = km.group(1).lower()
        val = km.group(2) or km.group(3) or km.group(4)
        if val and val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            items = re.findall(r"""['"]([^'"]+)['"]""", inner)
            out[key] = items
        else:
            out[key] = val
    return out


def find_model_yaml_entry(target: Path, project_root: Path, model_name: str) -> dict:
    """Walk up from target's dir looking for *.yml that declares this model."""
    for d in [target.parent, *target.parent.parents]:
        if not d.is_relative_to(project_root):
            break
        for yml in d.glob("*.yml"):
            try:
                data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            for m in (data.get("models") or []):
                if isinstance(m, dict) and m.get("name") == model_name:
                    return m
        if d == project_root:
            break
    return {}


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: parse_target.py <target_sql> <project_root>", file=sys.stderr)
        return 2

    target = Path(sys.argv[1]).resolve()
    project_root = Path(sys.argv[2]).resolve()
    raw = target.read_text(encoding="utf-8", errors="replace")
    clean = strip_comments(raw)

    refs = [
        {"name": m.group(1), "line": line_of(clean, m.start()), "raw": m.group(0)}
        for m in REF_RE.finditer(clean)
    ]
    sources = [
        {
            "source": m.group(1),
            "table": m.group(2),
            "line": line_of(clean, m.start()),
            "raw": m.group(0),
        }
        for m in SOURCE_RE.finditer(clean)
    ]

    # Env from config block (on raw, not clean, so comments inside config still parse)
    cfg = parse_config_block(raw)
    catalog = cfg.get("catalog") or cfg.get("database")
    schema = cfg.get("schema")
    tags = cfg.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    sources_used = []
    if catalog or schema or tags:
        sources_used.append("config_block")

    # Env from sibling YAML model stanza
    model_yaml = find_model_yaml_entry(target, project_root, target.stem)
    if model_yaml:
        ycfg = model_yaml.get("config") or {}
        catalog = catalog or ycfg.get("catalog") or ycfg.get("database")
        schema = schema or ycfg.get("schema")
        y_tags = model_yaml.get("tags") or ycfg.get("tags") or []
        if isinstance(y_tags, str):
            y_tags = [y_tags]
        # union, preserving order
        for t in y_tags:
            if t not in tags:
                tags.append(t)
        meta_env = (model_yaml.get("meta") or {}).get("env")
        if ycfg or y_tags or meta_env:
            sources_used.append("sibling_yaml")
    else:
        meta_env = None

    tags_lower = [t.lower() for t in tags if isinstance(t, str)]
    env_tags = extract_env_tags(tags_lower)

    out = {
        "target": str(target),
        "refs": refs,
        "sources": sources,
        "env": {
            "catalog": catalog.lower() if isinstance(catalog, str) else None,
            "schema": schema.lower() if isinstance(schema, str) else None,
            "tags": tags_lower,
            "env_tags": env_tags,
            "meta_env": meta_env.lower() if isinstance(meta_env, str) else None,
            "from": sources_used,
        },
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
