"""Episode artifacts on disk. Output is byte-for-byte deterministic for a given episode.

<dir>/public/   manifest.json  policy.json  ledger.csv  invoices.jsonl  payments.jsonl  fx.csv
<dir>/private/  truth.json  provenance.json        (evaluator only; never mounted for the agent)

Encoding: UTF-8, LF line endings, JSON with sorted-insertion order as generated (2-space indent
for .json, one compact object per line for .jsonl). In CSV a missing reference is the literal
`null`, mirroring the explicit JSON null of the contract.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .generator import Episode

LEDGER_COLUMNS = ["row_id", "timestamp", "account", "debit_minor", "credit_minor", "currency", "entity_id",
                  "invoice_id", "payment_id"]
FX_COLUMNS = ["date", "source_currency", "target_currency", "rate"]
NULL = "null"
PUBLIC_FILES = ("manifest.json", "policy.json", "ledger.csv", "invoices.jsonl", "payments.jsonl", "fx.csv")
PRIVATE_FILES = ("truth.json", "provenance.json")


def _json_text(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _jsonl_text(rows: list) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)


def _csv_text(rows: list, columns: list) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([NULL if r[c] is None else r[c] for c in columns])
    return buf.getvalue()


def render_files(ep: Episode) -> dict:
    """{relative path: text} for every artifact, public and private."""
    return {
        "public/manifest.json": _json_text(ep.manifest),
        "public/policy.json": _json_text(ep.policy),
        "public/ledger.csv": _csv_text(ep.tables["ledger"], LEDGER_COLUMNS),
        "public/invoices.jsonl": _jsonl_text(ep.tables["invoices"]),
        "public/payments.jsonl": _jsonl_text(ep.tables["payments"]),
        "public/fx.csv": _csv_text(ep.tables["fx"], FX_COLUMNS),
        "private/truth.json": _json_text(ep.truth),
        "private/provenance.json": _json_text(ep.provenance),
    }


def write_episode(ep: Episode, out_dir) -> Path:
    out_dir = Path(out_dir)
    for rel, text in render_files(ep).items():
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return out_dir


def _read_ledger(text: str) -> list:
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        row = {}
        for c in LEDGER_COLUMNS:
            v = r[c]
            if v == NULL:
                row[c] = None
            elif c in ("debit_minor", "credit_minor"):
                row[c] = int(v)
            else:
                row[c] = v
        rows.append(row)
    return rows


def load_episode(ep_dir, with_private: bool = True) -> Episode:
    ep_dir = Path(ep_dir)

    def read(rel):
        return (ep_dir / rel).read_bytes().decode("utf-8")

    manifest = json.loads(read("public/manifest.json"))
    tables = {
        "ledger": _read_ledger(read("public/ledger.csv")),
        "invoices": [json.loads(x) for x in read("public/invoices.jsonl").splitlines() if x],
        "payments": [json.loads(x) for x in read("public/payments.jsonl").splitlines() if x],
        "fx": [dict(r) for r in csv.DictReader(io.StringIO(read("public/fx.csv")))],
    }
    truth = json.loads(read("private/truth.json")) if with_private else None
    provenance = json.loads(read("private/provenance.json")) if with_private else None
    return Episode(manifest["episode_id"], manifest, json.loads(read("public/policy.json")), tables, truth, provenance)
