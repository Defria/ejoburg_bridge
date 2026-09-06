"""HTML-only statement metadata extraction (v2).

This module is a non-breaking addition alongside :mod:`.api`. It produces
the same dict shapes that :class:`.portal_api.PortalApi` produces for statement
rows and per-statement metadata, but sources every field from structured
HTML (the JSF statement-history list response and the ``/ViewBill`` page)
rather than pypdf text extraction of the statement PDF body.

Behavior contract (vs v1):

* ``rows[i]`` keys: ``index``, ``statement_date``, ``due_date``,
  ``download_button``, ``bill_amount``, ``balance``, ``invoice_number``.
  The first five match v1; ``invoice_number`` is added (v1 didn't expose it).
* ``parse_view_bill_html()`` returns ``{statement_date, due_date,
  amount_due, amount_due_source, account_number}`` mirroring
  ``PortalApi.parse_statement_pdf`` output keys actually consumed by the
  coordinator. ``amount_guess`` and ``text_excerpt`` are intentionally
  omitted; coordinator does not use them.
* All dates are normalized to ``YYYY/MM/DD`` to match v1's preferred
  emission format.
* For historical rows where ``/ViewBill`` does not expose the original
  per-statement amount due, ``amount_due`` is ``None`` and
  ``amount_due_source`` is ``"unavailable"``.
"""

from __future__ import annotations

import re
from typing import Any


_DATE_RE = re.compile(
    r"\b(\d{2}-\d{2}-\d{4}|\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2})\b"
)


def _normalize_date(raw: str | None) -> str | None:
    """Normalize any of DD-MM-YYYY, YYYY-MM-DD, YYYY/MM/DD to YYYY/MM/DD."""
    if not raw:
        return None
    raw = raw.strip()
    m = re.fullmatch(r"(\d{4})/(\d{2})/(\d{2})", raw)
    if m:
        return raw
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", raw)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    return None


def _parse_money(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = raw.replace("R", "").replace("\xa0", "").replace(" ", "").replace(",", "")
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Statement list HTML (the CDATA payload inside the partial-response update)
# ---------------------------------------------------------------------------

# Per-row block on the statement-history list is keyed on the index inside
# the PrimeFaces datatable id "j_idt126:<index>:". That id prefix is the
# only structural anchor we rely on; the leaf widget ids (j_idt127, j_idt140,
# ...) drift between deploys, so we anchor on visible label text instead.
_ROW_INDEX_RE = re.compile(
    r"historyForm:statementHistory:j_idt126:(\d+):"
)
_DOWNLOAD_BUTTON_RE = re.compile(
    r'name="(historyForm:statementHistory:j_idt126:\d+:[^"]*j_idt168[^"]*:commandButton)"'
)
# Fallback button match if PrimeFaces renames j_idt168.
_ANY_BUTTON_RE = re.compile(
    r'name="(historyForm:statementHistory:j_idt126:\d+:[^"]*:commandButton)"'
)


def _slice_row_blocks(panel_html: str) -> dict[int, str]:
    """Split panel HTML into per-row substrings keyed by row index.

    Each row substring starts at the first occurrence of its
    ``j_idt126:N:`` marker and ends just before the next row's marker
    (or at EOF for the last row).
    """
    starts: list[tuple[int, int]] = []  # (index, position)
    seen: set[int] = set()
    for m in _ROW_INDEX_RE.finditer(panel_html):
        idx = int(m.group(1))
        if idx in seen:
            continue
        seen.add(idx)
        starts.append((idx, m.start()))
    starts.sort(key=lambda t: t[1])

    blocks: dict[int, str] = {}
    for i, (idx, pos) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(panel_html)
        blocks[idx] = panel_html[pos:end]
    return blocks


def _row_label_text(block: str, label: str) -> str | None:
    """Return the bare text after ``<label>{label}</label> <br /> {value}<``.

    Used for date fields rendered as plain text (Due Date, Statement Date).
    """
    pattern = re.compile(
        re.escape(label) + r"</label>\s*(?:<br\s*/?>)?\s*([^<]+)<",
        re.IGNORECASE,
    )
    m = pattern.search(block)
    if not m:
        return None
    return m.group(1).strip() or None


def _row_label_input_value(block: str, label: str) -> str | None:
    """Return the first ``<input ... value="X">`` after ``<label>{label}</label>``.

    Used for numeric fields (Current Charges, Total) which PrimeFaces
    renders as ``ui-inputnumber`` with a readonly input.
    """
    label_idx = block.find(f">{label}</label>")
    if label_idx < 0:
        return None
    after = block[label_idx:]
    m = re.search(r'<input[^>]*value="([^"]*)"', after)
    if not m:
        return None
    return m.group(1)


def _row_label_following_label_text(block: str, label: str) -> str | None:
    """Return the text inside the next ``<label>...</label>`` after the
    given label heading. Used for Invoice Number which is rendered as
    a sibling output label rather than plain text."""
    label_idx = block.find(f">{label}</label>")
    if label_idx < 0:
        return None
    after = block[label_idx + len(label) + len("</label>"):]
    m = re.search(r"<label[^>]*>([^<]+)</label>", after)
    if not m:
        return None
    return m.group(1).strip() or None


def parse_statement_list_rows(panel_html: str) -> list[dict[str, Any]]:
    """Parse the PrimeFaces statement-history datatable update payload.

    Input is the CDATA contents of
    ``<update id="historyForm:statementHistory:daPanel">...</update>``.

    Returns a list of row dicts in ascending index order.
    """
    blocks = _slice_row_blocks(panel_html)
    rows: list[dict[str, Any]] = []
    for index in sorted(blocks):
        block = blocks[index]

        # Download button: prefer j_idt168 (matches v1 logic) then any.
        button_match = _DOWNLOAD_BUTTON_RE.search(block)
        if not button_match:
            button_match = _ANY_BUTTON_RE.search(block)
        download_button = button_match.group(1) if button_match else None

        invoice_number = _row_label_following_label_text(block, "Invoice Number")
        statement_date = _normalize_date(_row_label_text(block, "Statement Date"))
        due_date = _normalize_date(_row_label_text(block, "Due Date"))
        bill_amount = _parse_money(_row_label_input_value(block, "Current Charges"))
        balance = _parse_money(_row_label_input_value(block, "Total"))

        rows.append(
            {
                "index": index,
                "statement_date": statement_date,
                "download_button": download_button,
                "bill_amount": bill_amount,
                "balance": balance,
                "due_date": due_date,
                "invoice_number": invoice_number,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# /ViewBill HTML
# ---------------------------------------------------------------------------

_VIEWBILL_ACCOUNT_RE = re.compile(
    r"Account\s*Number\s*:\s*(\d{6,12})", re.IGNORECASE
)
# #due-values renders as: <p>{amount_due}</p><div class="horizontal-divide"></div><p>{due_date}</p>
_VIEWBILL_DUE_VALUES_RE = re.compile(
    r'<div\s+id="due-values"[^>]*>\s*'
    r"<p>\s*([^<]*?)\s*</p>\s*"
    r'<div\s+class="horizontal-divide"></div>\s*'
    r"<p>\s*([^<]*?)\s*</p>",
    re.IGNORECASE | re.DOTALL,
)


def parse_view_bill_html(html: str) -> dict[str, Any]:
    """Parse the static ``/ViewBill`` HTML page.

    Returns a dict with the same keys the coordinator consumes from
    :meth:`.portal_api.PortalApi.parse_statement_pdf`: ``statement_date``,
    ``due_date``, ``amount_due``, ``amount_due_source``,
    ``account_number``.

    ``statement_date`` is always ``None`` here because /ViewBill renders
    it via JS (``<p id="invoiceNumber">`` and friends are empty in static
    HTML). The list HTML provides statement_date per row, so callers
    should merge.
    """
    account = _VIEWBILL_ACCOUNT_RE.search(html)
    account_number = account.group(1) if account else None

    amount_due: float | None = None
    due_date: str | None = None
    amount_due_source = "unavailable"

    due_match = _VIEWBILL_DUE_VALUES_RE.search(html)
    if due_match:
        amount_due = _parse_money(due_match.group(1))
        due_date = _normalize_date(due_match.group(2))
        if amount_due is not None:
            amount_due_source = "view_bill_due_values"

    return {
        "account_number": account_number,
        "amount_due": amount_due,
        "amount_due_source": amount_due_source,
        "statement_date": None,
        "due_date": due_date,
    }


# ---------------------------------------------------------------------------
# Coordinator-facing helpers
# ---------------------------------------------------------------------------

def merge_row_pdf_parsed(
    row: dict[str, Any],
    view_bill: dict[str, Any] | None,
    *,
    is_latest: bool,
) -> dict[str, Any]:
    """Build the ``pdf_parsed`` dict for a row.

    For non-latest rows, ``view_bill`` may be ``None`` (we don't fetch
    /ViewBill for every historical row in v2). In that case
    ``amount_due`` is ``None`` and ``amount_due_source`` is
    ``"unavailable"``, per option-1 contract.
    """
    statement_date = row.get("statement_date")
    due_date = row.get("due_date")
    amount_due: float | None = None
    amount_due_source = "unavailable"

    if view_bill is not None:
        if view_bill.get("due_date"):
            due_date = view_bill["due_date"]
        if view_bill.get("amount_due") is not None:
            amount_due = view_bill["amount_due"]
            amount_due_source = view_bill.get(
                "amount_due_source", "view_bill_due_values"
            )

    return {
        "statement_date": statement_date,
        "due_date": due_date,
        "amount_due": amount_due,
        "amount_due_source": amount_due_source,
    }
