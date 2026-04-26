#!/usr/bin/env python3
"""Diff v1 (pypdf) vs v2 (HTML-only) statement metadata against a live account.

Run outside Home Assistant. Logs in once, reuses the session for both
parses, and prints a structured diff of the per-row ``pdf_parsed`` dict
plus the latest-statement ``parsed`` dict.

Usage::

    python3 tools/diff_statement_v1_v2.py \\
        --base-url https://www.e-joburg.org.za \\
        --username 'you@example.com' \\
        --password '...' \\
        --account 557586123

Credentials can also come from env: EJOBURG_BASE_URL / EJOBURG_USERNAME /
EJOBURG_PASSWORD / EJOBURG_ACCOUNT.

The script imports :mod:`api` and :mod:`api_v2` directly from the
custom component; it does NOT need Home Assistant.

Exit codes:
  0  v1 and v2 match for every comparable field
  1  diffs found (printed to stdout)
  2  invocation / runtime error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Make the custom component importable regardless of cwd.
HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT_DIR = os.path.normpath(
    os.path.join(HERE, "..", "custom_components", "ejoburg_bridge")
)
sys.path.insert(0, COMPONENT_DIR)

import api  # noqa: E402
import api_v2  # noqa: E402


def _get_creds(args: argparse.Namespace) -> tuple[str, str, str, str]:
    base_url = args.base_url or os.environ.get(
        "EJOBURG_BASE_URL", "https://www.e-joburg.org.za"
    )
    username = args.username or os.environ.get("EJOBURG_USERNAME")
    password = args.password or os.environ.get("EJOBURG_PASSWORD")
    account = args.account or os.environ.get("EJOBURG_ACCOUNT")
    missing = [
        name
        for name, val in [
            ("--username/EJOBURG_USERNAME", username),
            ("--password/EJOBURG_PASSWORD", password),
            ("--account/EJOBURG_ACCOUNT", account),
        ]
        if not val
    ]
    if missing:
        raise SystemExit("Missing credentials: " + ", ".join(missing))
    return base_url, username, password, account  # type: ignore[return-value]


def _round_money(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    return value


def _comparable_pdf_parsed(d: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(d, dict):
        return {}
    return {
        "statement_date": d.get("statement_date"),
        "due_date": d.get("due_date"),
        "amount_due": _round_money(d.get("amount_due")),
        # amount_due_source intentionally omitted from equality (different by design).
    }


def _diff_rows(
    v1_rows: list[dict[str, Any]], v2_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    by_idx_v1 = {r.get("index"): r for r in v1_rows}
    by_idx_v2 = {r.get("index"): r for r in v2_rows}
    all_idx = sorted(set(by_idx_v1) | set(by_idx_v2))
    for idx in all_idx:
        a = by_idx_v1.get(idx, {})
        b = by_idx_v2.get(idx, {})
        per_row: dict[str, Any] = {}
        for key in ("statement_date", "due_date", "bill_amount", "balance"):
            av, bv = a.get(key), b.get(key)
            if isinstance(av, float) or isinstance(bv, float):
                av, bv = _round_money(av), _round_money(bv)
            if av != bv:
                per_row[key] = {"v1": av, "v2": bv}
        a_pdf = _comparable_pdf_parsed(a.get("pdf_parsed"))
        b_pdf = _comparable_pdf_parsed(b.get("pdf_parsed"))
        for key in ("statement_date", "due_date", "amount_due"):
            if a_pdf.get(key) != b_pdf.get(key):
                per_row.setdefault("pdf_parsed", {})[key] = {
                    "v1": a_pdf.get(key),
                    "v2": b_pdf.get(key),
                }
        if per_row:
            diffs.append({"index": idx, "diff": per_row})
    return diffs


def _build_v1_view(client: api.EJoburgApi) -> dict[str, Any]:
    """Mirror the v1 coordinator path: list -> per-row PDF download + parse."""
    overview = client.get_account_overview()
    history = client.get_statement_history()
    rows = list(history.get("rows") or [])
    form_fields = history.get("form_fields") if isinstance(history, dict) else None
    form_fields_dict = form_fields if isinstance(form_fields, dict) else None

    enriched: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out = dict(row)
        button = str(out.get("download_button") or "").strip()
        if button:
            try:
                pdf_bytes = client.download_statement_pdf(
                    button, form_fields=form_fields_dict
                )
                parsed = client.parse_statement_pdf(pdf_bytes)
                out["pdf_parsed"] = {
                    "statement_date": parsed.get("statement_date"),
                    "due_date": parsed.get("due_date"),
                    "amount_due": parsed.get("amount_due"),
                    "amount_due_source": parsed.get("amount_due_source"),
                }
            except Exception as exc:
                out["pdf_parsed_error"] = str(exc)
        enriched.append(out)
    return {"overview": overview, "rows": enriched}


def _build_v2_view(client: api.EJoburgApi) -> dict[str, Any]:
    """Mirror the v2 coordinator path: list HTML for everything,
    pdf_parsed.amount_due sourced from list-row Total."""
    overview = client.get_account_overview()
    history = client.get_statement_history()
    panel_html = history.get("panel_html") or ""
    rows = api_v2.parse_statement_list_rows(panel_html) if panel_html else []

    enriched: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        out = dict(row)
        list_total = row.get("balance")
        if isinstance(list_total, (int, float)):
            amount_due_value: float | None = float(list_total)
            amount_due_source = "list_total"
        else:
            amount_due_value = None
            amount_due_source = "unavailable"
        out["pdf_parsed"] = {
            "statement_date": row.get("statement_date"),
            "due_date": row.get("due_date"),
            "amount_due": amount_due_value,
            "amount_due_source": amount_due_source,
        }
        enriched.append(out)
    return {"overview": overview, "rows": enriched}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--account")
    parser.add_argument(
        "--out-dir",
        default="/tmp/ejoburg_diff",
        help="Where to write v1.json / v2.json / diff.json",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the diff summary, not full snapshots",
    )
    args = parser.parse_args()

    try:
        base_url, username, password, account = _get_creds(args)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    os.makedirs(args.out_dir, exist_ok=True)

    client = api.EJoburgApi(base_url)
    client.login(username, password)

    print("Fetching v1 (pypdf) view...", file=sys.stderr)
    v1 = _build_v1_view(client)
    print("Fetching v2 (HTML-only) view...", file=sys.stderr)
    v2 = _build_v2_view(client)

    with open(os.path.join(args.out_dir, "v1.json"), "w", encoding="utf-8") as fh:
        json.dump(v1, fh, indent=2, default=str)
    with open(os.path.join(args.out_dir, "v2.json"), "w", encoding="utf-8") as fh:
        json.dump(v2, fh, indent=2, default=str)

    diffs = _diff_rows(v1["rows"], v2["rows"])
    summary = {
        "account": account,
        "v1_row_count": len(v1["rows"]),
        "v2_row_count": len(v2["rows"]),
        "rows_with_diffs": len(diffs),
        "diffs": diffs,
    }
    with open(os.path.join(args.out_dir, "diff.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)

    if not args.quiet:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(
            f"v1 rows={summary['v1_row_count']} "
            f"v2 rows={summary['v2_row_count']} "
            f"diffs={summary['rows_with_diffs']}"
        )
    print(f"Artifacts: {args.out_dir}/{{v1,v2,diff}}.json", file=sys.stderr)
    return 0 if not diffs else 1


if __name__ == "__main__":
    raise SystemExit(main())
