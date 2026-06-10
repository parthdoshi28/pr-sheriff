#!/usr/bin/env python3
"""Compare the target's env-tags to each resolved ref/source.

Env model
---------
Deployment envs are encoded as tags of the shape `<region>_<env>`, e.g.:

    {{ config(tags=["us_prd", "eu_prd", "us_dev", "eu_dev"]) }}

A model carrying multiple env-tags ships to all those envs. For a ref or
source to be safe to use from such a model, the referenced object must
also exist in every env the target ships to. In other words:

    target.env_tags  must be a subset of  ref.env_tags

The ref may carry extra envs (e.g. a raw source deployed everywhere) -
that's fine. It must not be missing any env the target ships to.

Promotion opportunities
-----------------------
After per-ref checks, the script computes the intersection of env-tags
across ALL resolved refs/sources. If that intersection contains envs the
target doesn't have, those are safe promotion targets: every dependency
already exists there. These are emitted as WARN with kind="promotion".

Usage:
    python check_env.py <parsed.json> <refs.json>

Stdout: JSON list of:
    {
      "kind":    "ref" | "source" | "promotion",
      "raw":     "{{ ref('stg_orders') }}" | "<promotion_opportunity>",
      "line":    12 | 0,
      "status":  "PASS" | "FAIL" | "WARN",
      "detail":  "<human readable>"
    }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def fmt(envs):
    return "{" + ", ".join(sorted(envs)) + "}" if envs else "{}"


def _load_json(path: Path):
    """Read JSON tolerating BOMs and PowerShell's default UTF-16LE redirect output."""
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return json.loads(data.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(data.decode("utf-8", errors="replace"))


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_env.py <parsed.json> <refs.json>", file=sys.stderr)
        return 2

    parsed = _load_json(Path(sys.argv[1]))
    refs = _load_json(Path(sys.argv[2]))

    target_env = parsed.get("env") or {}
    t_envs = set(target_env.get("env_tags") or [])
    t_cat = target_env.get("catalog")
    t_schema = target_env.get("schema")

    results: list[dict] = []
    resolved_ref_envs: list[set[str]] = []

    for r in refs:
        if r["status"] == "FAIL" or r.get("resolved_env") is None:
            continue
        renv = r["resolved_env"]
        r_envs = set(renv.get("env_tags") or [])
        r_cat = renv.get("catalog")
        r_schema = renv.get("schema")

        if r_envs:
            resolved_ref_envs.append(r_envs)

        # Subset rule on env-tags
        if not t_envs and not r_envs:
            results.append({
                "kind": r["kind"], "raw": r["raw"], "line": r["line"],
                "status": "WARN",
                "detail": "no env-tags on either side - env cannot be verified",
            })
        elif not t_envs:
            results.append({
                "kind": r["kind"], "raw": r["raw"], "line": r["line"],
                "status": "WARN",
                "detail": f"target has no env-tags; ref env_tags={fmt(r_envs)}",
            })
        elif not r_envs:
            results.append({
                "kind": r["kind"], "raw": r["raw"], "line": r["line"],
                "status": "WARN",
                "detail": f"ref has no env-tags; target env_tags={fmt(t_envs)} - cannot verify",
            })
        else:
            missing = t_envs - r_envs
            if not missing:
                extra = r_envs - t_envs
                detail = f"target env_tags {fmt(t_envs)} ⊆ ref env_tags {fmt(r_envs)}"
                if extra:
                    detail += f" (ref also deploys to {fmt(extra)})"
                results.append({
                    "kind": r["kind"], "raw": r["raw"], "line": r["line"],
                    "status": "PASS", "detail": detail,
                })
            else:
                results.append({
                    "kind": r["kind"], "raw": r["raw"], "line": r["line"],
                    "status": "FAIL",
                    "detail": (
                        f"ref is missing env-tags {fmt(missing)} that target ships to "
                        f"(target={fmt(t_envs)}, ref={fmt(r_envs)})"
                    ),
                })

        # Catalog / schema info-only warnings (do not FAIL)
        if t_cat and r_cat and t_cat != r_cat:
            results.append({
                "kind": r["kind"], "raw": r["raw"], "line": r["line"],
                "status": "WARN",
                "detail": f"catalog differs (target=`{t_cat}` vs ref=`{r_cat}`) - informational",
            })
        if t_schema and r_schema and t_schema != r_schema:
            results.append({
                "kind": r["kind"], "raw": r["raw"], "line": r["line"],
                "status": "WARN",
                "detail": f"schema differs (target=`{t_schema}` vs ref=`{r_schema}`) - informational",
            })

    # Promotion opportunities: envs where ALL refs exist but target doesn't.
    if resolved_ref_envs and t_envs:
        common_ref_envs = resolved_ref_envs[0]
        for s in resolved_ref_envs[1:]:
            common_ref_envs = common_ref_envs & s
        promotable = common_ref_envs - t_envs
        if promotable:
            results.append({
                "kind": "promotion",
                "raw": "<promotion_opportunity>",
                "line": 0,
                "status": "WARN",
                "detail": (
                    f"all dependencies support env-tags {fmt(promotable)} "
                    f"that the target does not deploy to — "
                    f"target could be promoted to {fmt(promotable)}"
                ),
            })

    json.dump(results, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
