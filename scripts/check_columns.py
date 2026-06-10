#!/usr/bin/env python3
"""Verify that qualified column references in the target SQL exist in the
referenced model's schema YAML or source YAML ``columns:`` declarations,
and flag backtick-quoted column aliases containing spaces (which break dbt).

Usage:
    python check_columns.py <target_sql> <parsed.json> <refs.json> <discovery.json>

Stdout: JSON list of:
    {
      "kind":    "ref" | "source" | "alias_naming",
      "raw":     "{{ ref('stg_orders') }}" | "AS `Institution Name`",
      "alias":   "orders" | null,
      "column":  "customer_id" | "Institution Name",
      "line":    15,
      "status":  "PASS" | "FAIL" | "WARN",
      "detail":  "<human readable>"
    }

Checks performed:
  1. Column existence -- qualified ``alias.column`` references are checked
     against the YAML ``columns:`` declarations of the resolved ref/source.
  2. Alias naming -- backtick-quoted aliases with spaces (e.g.
     ``AS `Institution Name` ``) are flagged as FAIL because they break
     dbt compilation.

Scope limitations:
  - Only *qualified* column references (``alias.column``) are checked.
    Bare column names without a table prefix are skipped.
  - Column declarations must exist in YAML.  Referenced models' SELECT
    clauses are NOT parsed (that would require dbt compile or a SQL parser).
  - CTE-defined columns are not tracked -- only columns from ref()/source()
    aliases.
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


# ---------------------------------------------------------------------------
# Shared helpers (mirrors parse_target.py / check_refs.py conventions)
# ---------------------------------------------------------------------------

REF_RE = re.compile(
    r"""\{\{\s*ref\(\s*['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?\s*\)\s*\}\}""",
    re.VERBOSE,
)
SOURCE_RE = re.compile(
    r"""\{\{\s*source\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*\}\}""",
    re.VERBOSE,
)

# Matches ``alias.column`` where *alias* is a known table reference and
# *column* is a plain identifier (not a star, number, or Jinja expression).
QUALIFIED_COL_RE = re.compile(
    r"\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\b"
)

# Backtick-quoted column alias with whitespace inside, e.g. AS `Institution Name`.
# These break dbt compilation on most warehouses.
BACKTICK_ALIAS_RE = re.compile(
    r"""\bAS\s+`([^`]*\s[^`]*)`""",
    re.IGNORECASE,
)

# SQL keywords that look like ``alias.column`` but aren't.
_SQL_KEYWORDS = frozenset({
    "group", "order", "partition", "cluster", "distribute", "sort",
    "cross", "inner", "outer", "left", "right", "full", "semi", "anti",
    "lateral", "natural", "using", "limit", "offset", "fetch", "union",
    "intersect", "except", "case", "when", "then", "else", "end", "cast",
    "over", "rows", "range", "between", "and", "or", "not", "in", "like",
    "is", "null", "true", "false", "select", "from", "where", "having",
    "join", "on", "as", "with", "into", "values", "set", "update",
    "delete", "insert", "create", "drop", "alter", "table", "view",
    "index", "exists", "distinct", "all", "any", "some", "asc", "desc",
})


def _load_json(path: Path):
    """Read JSON tolerating BOMs and PowerShell's UTF-16LE redirect output."""
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return json.loads(data.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(data.decode("utf-8", errors="replace"))


def strip_comments(sql: str) -> str:
    sql = re.sub(r"\{#.*?#\}", "", sql, flags=re.DOTALL)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


# ---------------------------------------------------------------------------
# Alias → ref/source mapping
# ---------------------------------------------------------------------------

# ``{{ ref('x') }}  AS alias``  or  ``{{ ref('x') }}  alias``
_ALIAS_AFTER = re.compile(
    r"""\}\}              # end of Jinja expression
        \s+               # whitespace
        (?:AS\s+)?        # optional AS keyword
        ([A-Za-z_]\w*)    # the alias
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _build_alias_map(clean_sql: str) -> dict[str, dict]:
    """Return {lowercase_alias: {kind, name/source+table, raw}} for every
    ref()/source() that has a table alias in the SQL."""
    alias_map: dict[str, dict] = {}

    for m in REF_RE.finditer(clean_sql):
        ref_name = m.group(1)
        raw = m.group(0)
        end_pos = m.end()
        after = _ALIAS_AFTER.match(clean_sql, end_pos)
        if after:
            alias = after.group(1).lower()
        else:
            alias = ref_name.lower()
        alias_map[alias] = {"kind": "ref", "name": ref_name, "raw": raw}

    for m in SOURCE_RE.finditer(clean_sql):
        src_name = m.group(1)
        tbl_name = m.group(2)
        raw = m.group(0)
        end_pos = m.end()
        after = _ALIAS_AFTER.match(clean_sql, end_pos)
        if after:
            alias = after.group(1).lower()
        else:
            alias = tbl_name.lower()
        alias_map[alias] = {
            "kind": "source",
            "source": src_name,
            "table": tbl_name,
            "raw": raw,
        }

    return alias_map


# ---------------------------------------------------------------------------
# Column declarations from YAML
# ---------------------------------------------------------------------------

def _model_columns_from_yaml(
    model_name: str,
    resolved_path: str | None,
    project_root: Path,
) -> list[str] | None:
    """Return declared column names for a model, or None if no columns: section."""
    if not resolved_path:
        return None

    model_path = project_root / resolved_path
    for d in [model_path.parent, *model_path.parent.parents]:
        if not d.is_relative_to(project_root):
            break
        for yml in d.glob("*.yml"):
            try:
                data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            for entry in (data.get("models") if isinstance(data, dict) else None) or []:
                if isinstance(entry, dict) and entry.get("name") == model_name:
                    cols = entry.get("columns")
                    if cols and isinstance(cols, list):
                        return [
                            c["name"].lower()
                            for c in cols
                            if isinstance(c, dict) and c.get("name")
                        ]
                    return None
        if d == project_root:
            break
    return None


def _source_columns_from_yaml(
    src_name: str,
    tbl_name: str,
    project_root: Path,
    source_yamls: list[str],
) -> list[str] | None:
    """Return declared column names for a source table, or None if absent."""
    for rel in source_yamls:
        path = project_root / rel
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        for src in (data.get("sources") or []):
            if not isinstance(src, dict) or src.get("name", "").lower() != src_name.lower():
                continue
            for tbl in (src.get("tables") or []):
                if not isinstance(tbl, dict) or tbl.get("name", "").lower() != tbl_name.lower():
                    continue
                cols = tbl.get("columns")
                if cols and isinstance(cols, list):
                    return [
                        c["name"].lower()
                        for c in cols
                        if isinstance(c, dict) and c.get("name")
                    ]
                return None
    return None


# ---------------------------------------------------------------------------
# Build a quick lookup: ref-name -> resolved_path from check_refs output
# ---------------------------------------------------------------------------

def _refs_resolved_path_index(refs_json: list[dict]) -> dict[str, str | None]:
    """Map lowercase ref name -> resolved_path from check_refs output."""
    idx: dict[str, str | None] = {}
    for r in refs_json:
        if r.get("kind") != "ref":
            continue
        raw = r["raw"]
        m = REF_RE.search(raw)
        if m:
            idx[m.group(1).lower()] = r.get("resolved_path")
    return idx


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) != 5:
        print(
            "usage: check_columns.py <target_sql> <parsed.json> <refs.json> <discovery.json>",
            file=sys.stderr,
        )
        return 2

    target_path = Path(sys.argv[1]).resolve()
    parsed = _load_json(Path(sys.argv[2]))
    refs_json = _load_json(Path(sys.argv[3]))
    discovery = _load_json(Path(sys.argv[4]))

    project_root = Path(discovery["project_root"])
    source_yamls = discovery.get("source_yamls", [])

    raw_sql = target_path.read_text(encoding="utf-8", errors="replace")
    clean = strip_comments(raw_sql)
    alias_map = _build_alias_map(clean)

    ref_path_idx = _refs_resolved_path_index(refs_json)

    # Collect declared columns per alias
    alias_columns: dict[str, list[str] | None] = {}
    for alias, info in alias_map.items():
        if info["kind"] == "ref":
            rpath = ref_path_idx.get(info["name"].lower())
            alias_columns[alias] = _model_columns_from_yaml(
                info["name"], rpath, project_root,
            )
        else:
            alias_columns[alias] = _source_columns_from_yaml(
                info["source"], info["table"], project_root, source_yamls,
            )

    # Scan for qualified column references
    seen: set[tuple[str, str]] = set()
    results: list[dict] = []

    for m in QUALIFIED_COL_RE.finditer(clean):
        alias_raw = m.group(1)
        col_raw = m.group(2)
        alias_lc = alias_raw.lower()
        col_lc = col_raw.lower()

        if alias_lc in _SQL_KEYWORDS or col_lc in _SQL_KEYWORDS:
            continue
        if alias_lc not in alias_map:
            continue

        pair = (alias_lc, col_lc)
        if pair in seen:
            continue
        seen.add(pair)

        info = alias_map[alias_lc]
        declared = alias_columns.get(alias_lc)
        ln = line_of(clean, m.start())

        if declared is None:
            label = (
                f"ref('{info['name']}')" if info["kind"] == "ref"
                else f"source('{info['source']}','{info['table']}')"
            )
            results.append({
                "kind": info["kind"],
                "raw": info["raw"],
                "alias": alias_raw,
                "column": col_raw,
                "line": ln,
                "status": "WARN",
                "detail": f"{label} has no columns declared in schema YAML — cannot verify '{col_raw}'",
            })
        elif col_lc in declared:
            results.append({
                "kind": info["kind"],
                "raw": info["raw"],
                "alias": alias_raw,
                "column": col_raw,
                "line": ln,
                "status": "PASS",
                "detail": f"'{col_raw}' found in declared columns",
            })
        else:
            label = (
                f"ref('{info['name']}')" if info["kind"] == "ref"
                else f"source('{info['source']}','{info['table']}')"
            )
            results.append({
                "kind": info["kind"],
                "raw": info["raw"],
                "alias": alias_raw,
                "column": col_raw,
                "line": ln,
                "status": "FAIL",
                "detail": (
                    f"'{col_raw}' not found in {label} declared columns "
                    f"{sorted(declared)}"
                ),
            })

    # Check for backtick-quoted aliases containing spaces (breaks dbt)
    for m in BACKTICK_ALIAS_RE.finditer(clean):
        alias_name = m.group(1)
        ln = line_of(clean, m.start())
        results.append({
            "kind": "alias_naming",
            "raw": m.group(0),
            "alias": None,
            "column": alias_name,
            "line": ln,
            "status": "FAIL",
            "detail": (
                f"backtick-quoted alias with spaces: `{alias_name}` — "
                "column aliases must be valid identifiers without spaces"
            ),
        })

    json.dump(results, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
