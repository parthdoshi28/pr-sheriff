#!/usr/bin/env python3
"""Resolve every ref()/source() in the parsed target against project files.

Usage:
    python check_refs.py <parsed.json> <discovery.json>

Stdout: JSON list. Each row:
    {
      "kind":          "ref" | "source",
      "raw":           "{{ ref('stg_orders') }}",
      "line":          12,
      "status":        "PASS" | "FAIL" | "WARN",
      "detail":        "<human readable>",
      "resolved_path": "models/staging/stg_orders.sql" | null,
      "resolved_env":  {"catalog":..., "schema":..., "tags":[...], "meta_env":...} | null
    }
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    print("pyyaml is required: pip install pyyaml", file=sys.stderr)
    sys.exit(3)


ENV_TAG_RE = re.compile(
    r"^[a-z]{2,5}_(prd|prod|dev|stg|staging|uat|qa|test)$",
    re.IGNORECASE,
)


def _lower(x):
    return x.lower() if isinstance(x, str) else None


def _load_json(path: Path):
    """Read JSON tolerating BOMs and PowerShell's default UTF-16LE redirect output."""
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return json.loads(data.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(data.decode("utf-8", errors="replace"))


def _env_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        if isinstance(t, str) and ENV_TAG_RE.match(t):
            tl = t.lower()
            if tl not in out:
                out.append(tl)
    return out


def index_models(project_root: Path, model_paths: list[str]) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = defaultdict(list)
    for rel in model_paths:
        idx[Path(rel).stem].append(rel)
    return idx


CONFIG_RE = re.compile(r"\{\{\s*config\((.*?)\)\s*\}\}", re.DOTALL)
KV_RE = re.compile(
    r"""(\w+)\s*=\s*(?:
        '([^']*)' |
        "([^"]*)" |
        (\[[^\]]*\])
    )""",
    re.VERBOSE,
)


def _config_block_tags(sql_text: str) -> tuple[list[str], str | None, str | None]:
    """Return (tags, catalog, schema) from a model's {{ config(...) }} block.

    Skips dict-valued kwargs like `databricks_tags={...}` so they don't
    pollute the tag list.
    """
    m = CONFIG_RE.search(sql_text)
    if not m:
        return [], None, None
    body = m.group(1)
    tags: list[str] = []
    catalog = schema = None
    for km in KV_RE.finditer(body):
        key = km.group(1).lower()
        val = km.group(2) or km.group(3) or km.group(4)
        if not val:
            continue
        if key == "tags" and val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            tags = re.findall(r"""['"]([^'"]+)['"]""", inner)
        elif key in ("catalog", "database"):
            catalog = val
        elif key == "schema":
            schema = val
    return tags, catalog, schema


def model_env(project_root: Path, model_rel: str) -> dict:
    """Resolve env for a referenced model from its .sql config block AND sibling YAML."""
    model_path = project_root / model_rel
    name = model_path.stem

    # 1. From the model's own .sql {{ config(...) }} block
    sql_tags: list[str] = []
    catalog = schema = None
    try:
        sql_text = model_path.read_text(encoding="utf-8", errors="replace")
        sql_tags, catalog, schema = _config_block_tags(sql_text)
    except Exception:
        pass

    # 2. From sibling YAML, walking up to project root
    yml_tags: list[str] = []
    meta_env = None
    for d in [model_path.parent, *model_path.parent.parents]:
        if not d.is_relative_to(project_root):
            break
        for yml in d.glob("*.yml"):
            try:
                data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            for m in (data.get("models") if isinstance(data, dict) else None) or []:
                if isinstance(m, dict) and m.get("name") == name:
                    cfg = m.get("config") or {}
                    t = m.get("tags") or cfg.get("tags") or []
                    if isinstance(t, str):
                        t = [t]
                    yml_tags.extend(t)
                    catalog = catalog or cfg.get("catalog") or cfg.get("database")
                    schema = schema or cfg.get("schema")
                    meta_env = meta_env or (m.get("meta") or {}).get("env")
        if d == project_root:
            break

    # Union tags, preserving order
    merged: list[str] = []
    for t in (*sql_tags, *yml_tags):
        if isinstance(t, str) and t.lower() not in merged:
            merged.append(t.lower())

    return {
        "catalog": _lower(catalog),
        "schema": _lower(schema),
        "tags": merged,
        "env_tags": _env_tags(merged),
        "meta_env": _lower(meta_env),
    }


def index_sources(project_root: Path, source_yamls: list[str]) -> dict[tuple[str, str], dict]:
    idx: dict[tuple[str, str], dict] = {}
    warns: list[str] = []
    for rel in source_yamls:
        path = project_root / rel
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            warns.append(f"malformed YAML: {rel} ({e})")
            continue
        for src in (data.get("sources") or []):
            if not isinstance(src, dict):
                continue
            src_name = src.get("name")
            src_db = src.get("database") or src.get("catalog")
            src_schema = src.get("schema")
            src_tags = src.get("tags") or []
            if isinstance(src_tags, str):
                src_tags = [src_tags]
            src_meta_env = (src.get("meta") or {}).get("env")
            for tbl in (src.get("tables") or []):
                if not isinstance(tbl, dict):
                    continue
                tname = tbl.get("name")
                if not src_name or not tname:
                    continue
                t_db = tbl.get("database") or tbl.get("catalog") or src_db
                t_schema = tbl.get("schema") or src_schema
                t_tags = tbl.get("tags") or src_tags
                if isinstance(t_tags, str):
                    t_tags = [t_tags]
                t_meta_env = (tbl.get("meta") or {}).get("env") or src_meta_env
                tags_lc = [t.lower() for t in t_tags if isinstance(t, str)]
                idx[(src_name.lower(), tname.lower())] = {
                    "yaml_path": rel,
                    "catalog": _lower(t_db),
                    "schema": _lower(t_schema),
                    "tags": tags_lc,
                    "env_tags": _env_tags(tags_lc),
                    "meta_env": _lower(t_meta_env),
                }
    return idx, warns  # type: ignore[return-value]


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_refs.py <parsed.json> <discovery.json>", file=sys.stderr)
        return 2

    parsed = _load_json(Path(sys.argv[1]))
    discovery = _load_json(Path(sys.argv[2]))
    project_root = Path(discovery["project_root"])

    model_idx = index_models(project_root, discovery.get("models", []))
    source_idx, source_warns = index_sources(project_root, discovery.get("source_yamls", []))

    results: list[dict] = []

    for w in source_warns:
        results.append({
            "kind": "source",
            "raw": "<sources.yml load>",
            "line": 0,
            "status": "WARN",
            "detail": w,
            "resolved_path": None,
            "resolved_env": None,
        })

    for r in parsed.get("refs", []):
        name = r["name"]
        matches = model_idx.get(name, [])
        if not matches:
            results.append({
                "kind": "ref",
                "raw": r["raw"],
                "line": r["line"],
                "status": "FAIL",
                "detail": f"ref('{name}') has no matching model in models/",
                "resolved_path": None,
                "resolved_env": None,
            })
        elif len(matches) > 1:
            results.append({
                "kind": "ref",
                "raw": r["raw"],
                "line": r["line"],
                "status": "WARN",
                "detail": f"ref('{name}') is ambiguous: {matches}",
                "resolved_path": matches[0],
                "resolved_env": model_env(project_root, matches[0]),
            })
        else:
            results.append({
                "kind": "ref",
                "raw": r["raw"],
                "line": r["line"],
                "status": "PASS",
                "detail": f"ref('{name}') → {matches[0]}",
                "resolved_path": matches[0],
                "resolved_env": model_env(project_root, matches[0]),
            })

    for s in parsed.get("sources", []):
        key = (s["source"].lower(), s["table"].lower())
        hit = source_idx.get(key)
        if not hit:
            results.append({
                "kind": "source",
                "raw": s["raw"],
                "line": s["line"],
                "status": "FAIL",
                "detail": f"source('{s['source']}','{s['table']}') not found in any sources.yml",
                "resolved_path": None,
                "resolved_env": None,
            })
        else:
            results.append({
                "kind": "source",
                "raw": s["raw"],
                "line": s["line"],
                "status": "PASS",
                "detail": f"source('{s['source']}','{s['table']}') → {hit['yaml_path']}",
                "resolved_path": hit["yaml_path"],
                "resolved_env": {
                    "catalog": hit["catalog"],
                    "schema": hit["schema"],
                    "tags": hit["tags"],
                    "meta_env": hit["meta_env"],
                },
            })

    json.dump(results, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
