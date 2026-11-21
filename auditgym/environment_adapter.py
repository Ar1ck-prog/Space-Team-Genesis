"""Public-only bridge from generator artifacts to the reset/step environment."""
from __future__ import annotations

import copy
from typing import Any

from space_team_genesis.environment import AuditEnvironment, EpisodeData, SubmissionEvaluator


def public_episode_data(episode: Any) -> EpisodeData:
    """Convert only manifest, policy and public tables; never read truth/provenance."""
    tables = episode.tables
    invoices = [dict(row, id=row["invoice_id"]) for row in tables["invoices"]]
    payments = [dict(row, id=row["payment_id"]) for row in tables["payments"]]
    documents = {row["doc_id"]: {"id": row["doc_id"], **{key: value for key, value in row.items() if key != "doc_id"}}
                 for row in [*tables["invoices"], *tables["payments"]]}
    return EpisodeData(
        episode_id=episode.episode_id, manifest=copy.deepcopy(episode.manifest),
        policy_summary=copy.deepcopy(episode.policy), documents=documents,
        records={
            "ledger": [{"id": row["row_id"], **{key: value for key, value in row.items() if key != "row_id"}}
                       for row in tables["ledger"]],
            "invoices": invoices, "payments": payments,
            "fx": [dict(row, id=f"fx:{row['date']}:{row['source_currency']}:{row['target_currency']}")
                   for row in tables["fx"]],
        },
    )


def environment_from_public_episode(episode: Any, evaluator: SubmissionEvaluator, *, evaluation_mode: str = "open") -> AuditEnvironment:
    return AuditEnvironment(data=public_episode_data(episode), evaluator=evaluator, evaluation_mode=evaluation_mode)
