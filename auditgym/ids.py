"""Canonical public identifiers (docs/API_CONTRACT.md section 2).

ASCII, 1..128 characters, no whitespace, case-sensitive (never normalized).
  ledger:<row_id>  invoice:<invoice_id>  payment:<payment_id>  doc:<doc_id>   local part has no ':'
  fx:<YYYY-MM-DD>:<SRC>:<TGT>                                               the only structured id
"""

from __future__ import annotations

import re

NAMESPACES = ("ledger", "invoice", "payment", "doc", "fx")
MAX_ID_LEN = 128

_SIMPLE = re.compile(r"^(ledger|invoice|payment|doc):[\x21-\x39\x3b-\x7e]+$")  # printable ASCII except ':'
_FX = re.compile(r"^fx:\d{4}-\d{2}-\d{2}:[A-Z]{3}:[A-Z]{3}$")


def namespace(id_: str) -> str | None:
    """Namespace of a well-formed canonical id, else None."""
    if not isinstance(id_, str) or not 1 <= len(id_) <= MAX_ID_LEN or not id_.isascii():
        return None
    m = _SIMPLE.match(id_)
    if m:
        return m.group(1)
    # Only the form fx:<YYYY-MM-DD>:<SRC>:<TGT> is structural (same rule as the environment's
    # validator); an fx id for a date that does not exist is well-formed and simply matches nothing.
    if _FX.match(id_):
        return "fx"
    return None


def canonical_array(ids: list) -> list:
    """Contract canonical form: exact duplicates removed, sorted lexicographically by full id."""
    return sorted(set(ids))
