"""Build static data for the frontend from the committed open episodes.

  python frontend/build_data.py

Writes frontend/data/*.js (plain `window.GENESIS_*` assignments, so the pages also work when
opened straight from disk via file://, where fetch() of local JSON is blocked).

Only public episode files are bundled. The one exception is the walkthrough of the demo
episode (dev seed 2013), whose answers are already published in docs/DEMO.md; its findings
are regenerated here from the public seed and scored by the real evaluator.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from auditgym.dataset import load_manifest  # noqa: E402
from auditgym.episode_io import load_episode  # noqa: E402
from auditgym.evaluator import Evaluator  # noqa: E402
from auditgym.generator import generate  # noqa: E402

OUT = ROOT / "frontend" / "data"
DEMO_SLOT = ("dev", "interaction_boundary_minus_small")


def js(name: str, obj) -> str:
    return f"window.{name} = {json.dumps(obj, ensure_ascii=False, separators=(',', ':'))};\n"


def reward_table(ep) -> list:
    """Score typical strategies on the demo episode with the real evaluator."""
    truth = ep.truth["issues"]
    oracle = [{"type": t["type"], "affected_ids": t["affected_ids"], "claimed_amount_minor": t["expected_amount_minor"],
               "currency": t["currency"], "evidence_ids": t["evidence_sets"][0], "explanation": "Supported."} for t in truth]
    read_all = sum(math.ceil(n / 50) for n in ep.manifest["record_counts"].values())
    base = ep.manifest["base_currency"]
    rows = ep.tables["ledger"]
    flags = []
    for i in range(30):
        r = rows[i]
        kind = ("fx_date_or_rate", "reporting_period")[i % 2]
        flags.append({"type": kind, "affected_ids": [r["row_id"]], "claimed_amount_minor": r["debit_minor"] or r["credit_minor"],
                      "currency": base, "evidence_ids": [r["row_id"]], "explanation": "Flag everything."})
    off = [dict(f, claimed_amount_minor=f["claimed_amount_minor"] + 1) for f in oracle]
    cases = [
        ("Полный ответ сразу (oracle)", "предел награды: все ошибки, ни одного лишнего шага", oracle, 0),
        ("Полный ответ после чтения всех таблиц", f"все ошибки, {read_all} шагов на чтение", oracle, read_all),
        ("Найдено 4 из 5", "одна ошибка пропущена", oracle[:-1], read_all),
        ("Каждый вывод отправлен дважды", "повторы считаются ложными", oracle + oracle, read_all),
        ("Суммы на 1 цент неточны", "допуск суммы нулевой", off, read_all),
        ("Молчание", "пустой ответ на книге с ошибками", [], read_all),
        ("Пометить всё", "30 обвинений по первым строкам книги", flags, 2),
    ]
    out = []
    for name, why, findings, steps in cases:
        ev = Evaluator.from_episode(ep, "open")
        res = ev.score_submission({"episode_id": ep.episode_id, "findings": findings}, steps)
        out.append({"name": name, "why": why, "steps": steps, "tp": res.tp, "fp": res.fp, "fn": res.fn,
                    "f1": round(res.f1, 4), "reward": round(res.reward, 4)})
    return out


def walkthrough(ep) -> list:
    """Human explanation for each demo issue (texts follow docs/DEMO.md)."""
    notes = {
        "duplicate_invoice": "Тот же клиент, те же позиции и сумма, тот же местный день, новый номер. Один документ напечатан в UTC, другой в местном времени, поэтому порядок виден только по моменту времени.",
        "payment_allocation": "Платёж ссылается на счёт и не больше его остатка, но погашено только 55%. Остаток ушёл на 2400 «Нераспределённые поступления».",
        "fx_date_or_rate": "Платёж напечатан как 1 января по UTC, но в Сан-Паулу это 31 декабря, 21:07. По политике нужен курс 31.12, а в кассе проведено по курсу 1 января.",
        "reporting_period": "Счёт напечатан со смещением −10:00 и выглядит как 31 декабря, но по времени компании это 1 января, 06:34. В книге проведён задним числом 31.12.",
    }
    return [{"type": t["type"], "scenario": t["scenario"], "affected_ids": t["affected_ids"],
             "amount_minor": t["expected_amount_minor"], "currency": t["currency"],
             "evidence_ids": t["evidence_sets"][0], "note": notes[t["type"]]} for t in ep.truth["issues"]]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    demo = None
    for split in ("train", "dev"):
        for e in load_manifest(split)["episodes"]:
            pub = load_episode(ROOT / "episodes" / split / e["episode_id"], with_private=False)
            payload = {"manifest": pub.manifest, "policy": pub.policy, "tables": pub.tables}
            (OUT / f"{e['episode_id']}.js").write_bytes(js("GENESIS_EPISODE", payload).encode("utf-8"))
            index.append({"split": split, "slot": e["slot"], "description": e["description"], "seed": e["seed"],
                          "episode_id": e["episode_id"], "size": e["config"]["company_size"], **e["summary"]})
            if (split, e["slot"]) == DEMO_SLOT:
                demo = e
    ep = generate(demo["seed"], demo["config"])
    extra = {"demo_episode_id": demo["episode_id"], "demo_seed": demo["seed"],
             "walkthrough": walkthrough(ep), "rewards": reward_table(ep)}
    (OUT / "index.js").write_bytes((js("GENESIS_INDEX", index) + js("GENESIS_DEMO", extra)).encode("utf-8"))
    print(f"{len(index)} episodes, demo {demo['episode_id']} -> {OUT}")
    for r in extra["rewards"]:
        print(f"  {r['reward']:+.3f}  F1={r['f1']:.3f}  {r['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
