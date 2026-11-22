"""Self-contained jury screen for the offline "Hide the error from AI" demo.

The renderer intentionally has no access to an episode seed, evaluator truth, or
matcher diagnostics.  The trusted local demo server supplies the small public
views documented in :func:`build_demo_page_payload`.
"""

from __future__ import annotations

import json
from typing import Any, Mapping


def build_demo_page_payload(
    case_view: Mapping[str, Any] | None = None,
    run_view: Mapping[str, Any] | None = None,
    batch_view: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the browser's initial, public-only payload.

    ``case_view`` is safe before an agent submits.  Its useful fields are
    ``case_id``, ``company_name``, ``currency``, ``operations`` (each with
    ``id``, ``document_number``, ``amount_minor`` and ``ledger_date``), and
    ``monthly_totals`` (``month``, ``amount_minor``).  It deliberately has no
    source-document date, seed, selected hidden variant, or expected finding.

    The local API used by the embedded JavaScript is deliberately short:

    * ``POST /api/v1/hide-error-demo/start`` receives ``{case_id, choice}``
      where choice is ``swap_months`` or ``keep_as_is`` and returns ``run_id``.
    * ``POST /api/v1/hide-error-demo/run`` first receives ``{run_id}`` and
      returns only ``opened_documents``.  A later ``{run_id, action:"submit"}``
      returns ``result`` with ``agent_conclusion`` and ``comparisons``.  Each
      comparison has ``ledger_date``, ``source_date``, ``agent_finding`` and
      ``outcome`` (``proved_error``, ``missed_error``, ``false_alarm``, or
      ``clear``).
    * ``POST /api/v1/hide-error-demo/batch`` receives ``{case_id}`` and
      returns ``summary``: ``total_cases``, ``inserted_errors``,
      ``proved_errors``, ``clean_cases``, and ``false_alarms``.

    ``run_view`` and ``batch_view`` are optional completed public views.  They
    make the renderer convenient to snapshot-test, but are never required by
    the normal interactive path.
    """

    default_case = {
        "case_id": "jury-demo",
        "company_name": "Genesis Demo LLP",
        "currency": "KZT",
        "operations": [
            {"id": "OP-01", "document_number": "INV-1041", "amount_minor": 40_000_000, "ledger_date": "2026-10-31"},
            {"id": "OP-02", "document_number": "INV-1042", "amount_minor": 40_000_000, "ledger_date": "2026-11-01"},
        ],
        "monthly_totals": [
            {"month": "Октябрь", "amount_minor": 40_000_000},
            {"month": "Ноябрь", "amount_minor": 40_000_000},
        ],
    }
    return {
        "case_view": dict(case_view or default_case),
        "run_view": dict(run_view or {}),
        "batch_view": dict(batch_view or {}),
    }


def render_demo_page(
    case_view: Mapping[str, Any] | None = None,
    run_view: Mapping[str, Any] | None = None,
    batch_view: Mapping[str, Any] | None = None,
) -> str:
    """Return a complete offline HTML page for the jury demo."""

    payload = json.dumps(
        build_demo_page_payload(case_view, run_view, batch_view),
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    return _PAGE.replace("__INITIAL_STATE__", payload)


_PAGE = r'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Genesis — Спрячь ошибку от ИИ</title>
  <style>
    :root { --ink:#f4f7ff; --muted:#aebbd6; --panel:#111b34; --panel2:#172544; --line:#2c3d63; --blue:#70a9ff; --green:#5ee6ac; --amber:#ffd166; --red:#ff7187; --bg:#080d1d; }
    * { box-sizing:border-box } body { margin:0; color:var(--ink); background:radial-gradient(circle at 76% -10%,#243b77 0,transparent 34rem),var(--bg); font:16px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
    main { max-width:1080px; margin:auto; padding:34px 22px 58px; } .eyebrow { color:var(--blue); font-size:.76rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; } h1 { margin:5px 0 8px; font-size:clamp(2rem,5vw,3.7rem); letter-spacing:-.055em; line-height:1; } .lead { color:var(--muted); max-width:740px; margin:0 0 27px; }
    .grid { display:grid; grid-template-columns:1.35fr .9fr; gap:17px; } .card { background:linear-gradient(145deg,rgba(23,37,68,.98),rgba(13,22,43,.98)); border:1px solid var(--line); border-radius:18px; padding:20px; box-shadow:0 18px 42px rgba(0,0,0,.18); } h2 { font-size:1.05rem; margin:0 0 14px; letter-spacing:-.02em; } .operation { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:14px 0; border-top:1px solid var(--line); } .operation:first-of-type { border-top:0; padding-top:0 }.op-id { color:var(--blue); font-weight:750; font-size:.86rem }.quiet { color:var(--muted); font-size:.87rem }.amount { font-weight:800; white-space:nowrap; font-variant-numeric:tabular-nums; } .total { display:flex; justify-content:space-between; padding:12px 0; border-top:1px solid var(--line); font-variant-numeric:tabular-nums; }
    .choices { display:grid; gap:9px; }.choice { display:flex; align-items:start; gap:10px; padding:12px; border:1px solid var(--line); border-radius:12px; cursor:pointer; }.choice:has(input:checked) { border-color:var(--blue); background:#1c3058; }.choice input { margin-top:4px; accent-color:var(--blue); } .choice strong { display:block; }.choice span { display:block; color:var(--muted); font-size:.84rem; margin-top:2px; }
    button { appearance:none; border:0; border-radius:11px; background:linear-gradient(120deg,#81b8ff,#5585ff); color:#07132a; font-weight:850; padding:12px 15px; cursor:pointer; font-size:.94rem; margin-top:16px; } button:hover { filter:brightness(1.08) } button:disabled { opacity:.55; cursor:wait } button.secondary { background:transparent; color:var(--ink); border:1px solid #4e6598; } .status { min-height:1.4em; color:var(--muted); font-size:.9rem; margin:11px 0 0; }
    #investigation, #result, #batch { margin-top:17px; } .hidden { display:none!important; }.docs { display:flex; flex-wrap:wrap; gap:8px; }.doc { padding:8px 10px; border-radius:999px; background:#1d315a; border:1px solid #3c5790; color:#dce9ff; font-size:.86rem; }.notice { padding:13px 15px; border-left:4px solid var(--amber); background:rgba(255,209,102,.09); border-radius:0 10px 10px 0; color:#ffe7a2; }.comparison { display:grid; grid-template-columns:.9fr 1fr 1.35fr; gap:0; border:1px solid var(--line); border-radius:13px; overflow:hidden; margin-top:10px; }.comparison > div { padding:13px; border-right:1px solid var(--line); }.comparison > div:last-child { border:0; }.comparison .label { display:block; color:var(--muted); font-size:.76rem; margin-bottom:3px; }.comparison.missed_error { border-color:var(--red); box-shadow:inset 4px 0 var(--red); }.comparison.false_alarm { border-color:var(--amber); box-shadow:inset 4px 0 var(--amber); }.comparison.proved_error { border-color:var(--green); box-shadow:inset 4px 0 var(--green); }.outcome { font-weight:800; }.missed_error .outcome { color:var(--red); }.false_alarm .outcome { color:var(--amber); }.proved_error .outcome { color:var(--green); }.clear .outcome { color:var(--muted); }.batch-stats { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }.stat { padding:13px; background:#0c1630; border:1px solid var(--line); border-radius:12px; }.stat strong { display:block; font-size:1.55rem; line-height:1.1; font-variant-numeric:tabular-nums; }.stat span { color:var(--muted); font-size:.78rem; }.error { color:#ff9baa; }
    @media(max-width:720px){ main{padding:25px 14px 42px}.grid{grid-template-columns:1fr}.comparison{grid-template-columns:1fr}.comparison>div{border-right:0;border-bottom:1px solid var(--line)}.comparison>div:last-child{border-bottom:0}.batch-stats{grid-template-columns:1fr 1fr} }
  </style>
</head>
<body><main>
  <div class="eyebrow">Space Team · Genesis / офлайн-демо</div><h1>Спрячь ошибку от ИИ</h1>
  <p class="lead">Обе месячные суммы сходятся. Попробуйте незаметно поменять периоды — а затем посмотрите, что ИИ сумел доказать только по открытым документам.</p>
  <section class="grid">
    <article class="card"><h2 id="company-title">Компания</h2><div id="operations"></div></article>
    <aside class="card"><h2>Выбор жюри</h2><div class="choices">
      <label class="choice"><input type="radio" name="choice" value="swap_months"><div><strong>Поменять месяцы местами</strong><span>Меняются только месяцы записей в учёте. Исходные документы не меняются.</span></div></label>
      <label class="choice"><input type="radio" name="choice" value="keep_as_is" checked><div><strong>Оставить как есть</strong><span>Чистый случай: в учёте остаются даты документов.</span></div></label>
    </div><button id="start">Запустить расследование</button><p class="status" id="status" aria-live="polite"></p></aside>
  </section>
  <section id="investigation" class="card hidden"><h2>Документы, которые действительно открыл агент</h2><div id="documents" class="docs"></div><p class="quiet">До сдачи ответа здесь нет эталона, seed или причины оценки.</p><button id="submit" class="secondary">Показать сданный ответ</button></section>
  <section id="result" class="card hidden"><h2>Сверка после сдачи ответа</h2><div id="conclusion" class="notice"></div><div id="comparisons"></div></section>
  <section id="batch" class="card"><h2>Проверка устойчивости</h2><p class="quiet">Новая серия из 10 компаний создаётся локально: 5 чистых и 5 с перестановкой, в перемешанном порядке.</p><button id="batch-button" class="secondary">Проверить ещё на 10 компаниях</button><div id="batch-results" class="hidden"></div></section>
</main>
<script>
(() => {
  const state = __INITIAL_STATE__;
  const $ = (id) => document.getElementById(id);
  let runId = null;
  const esc = (value) => String(value ?? "—").replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const money = (minor, currency) => { const n = Number(minor); if (!Number.isFinite(n)) return "—"; return new Intl.NumberFormat('ru-RU',{minimumFractionDigits:0,maximumFractionDigits:2}).format(n / 100) + ' ' + esc(currency || 'KZT'); };
  const call = async (path, data) => { const response = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); const json = await response.json().catch(()=>({})); if (!response.ok || json.error) throw new Error(json.error?.message || 'Локальный сервер не ответил.'); return json; };
  const outcomeText = { proved_error:'Ошибка доказана', missed_error:'Ошибка пропущена', false_alarm:'Ложная тревога', clear:'Ошибок нет' };
  function renderCase(view) { const c = view || {}; $('company-title').textContent = c.company_name || 'Компания'; $('operations').innerHTML = (c.operations || []).map(op => `<div class="operation"><div><div class="op-id">${esc(op.document_number || op.id)}</div><div class="quiet">Учёт: ${esc(op.ledger_date || op.ledger_month || 'месяц не указан')}</div></div><div class="amount">${money(op.amount_minor, c.currency)}</div></div>`).join('') + `<h2 style="margin:18px 0 0">Итог по месяцам</h2>` + (c.monthly_totals || []).map(total => `<div class="total"><span>${esc(total.month)}</span><strong>${money(total.amount_minor, c.currency)}</strong></div>`).join(''); }
  function renderDocuments(docs) { $('documents').innerHTML = (docs || []).length ? docs.map(d => `<span class="doc">${esc(d.label || d.document_number || d.id)}</span>`).join('') : '<span class="quiet">Агент не открыл документов.</span>'; }
  function renderResult(result) { $('result').classList.remove('hidden'); $('conclusion').textContent = result.agent_conclusion || result.summary || 'Агент сдал ответ.'; $('comparisons').innerHTML = (result.comparisons || []).map(row => `<article class="comparison ${esc(row.outcome || 'clear')}"><div><span class="label">Запись в учёте</span>${esc(row.ledger_date)}</div><div><span class="label">Дата исходного документа</span>${esc(row.source_date)}</div><div><span class="label">Вывод агента</span>${esc(row.agent_finding || 'Не заявлено')}<div class="outcome">${esc(outcomeText[row.outcome] || row.outcome || '')}</div></div></article>`).join('') || '<p class="quiet">Сравнений нет.</p>'; }
  function renderBatch(summary) { if (!summary) return; $('batch-results').classList.remove('hidden'); const values = [[summary.proved_errors ?? 0,'ошибок доказано'],[summary.inserted_errors ?? 0,'внесено ошибок'],[summary.false_alarms ?? 0,'ложных тревог'],[summary.clean_cases ?? 0,'чистых компаний']]; $('batch-results').innerHTML = `<div class="batch-stats" style="margin-top:16px">${values.map(([v,l])=>`<div class="stat"><strong>${esc(v)}</strong><span>${esc(l)}</span></div>`).join('')}</div>`; }
  renderCase(state.case_view); if (state.run_view.opened_documents) { renderDocuments(state.run_view.opened_documents); $('investigation').classList.remove('hidden'); } if (state.run_view.result) renderResult(state.run_view.result); renderBatch(state.batch_view.summary || state.batch_view);
  $('start').addEventListener('click', async () => { const button = $('start'); button.disabled = true; $('status').textContent = 'Запускаем локальное расследование…'; try { const choice = document.querySelector('input[name="choice"]:checked').value; const started = await call('/api/v1/hide-error-demo/start',{case_id:state.case_view.case_id,choice}); runId = started.run_id; const run = await call('/api/v1/hide-error-demo/run',{run_id:runId}); renderDocuments(run.opened_documents); $('investigation').classList.remove('hidden'); $('status').textContent = 'Агент изучил доступные документы. Теперь он готов сдать ответ.'; } catch (error) { $('status').innerHTML = `<span class="error">${esc(error.message)}</span>`; button.disabled = false; } });
  $('submit').addEventListener('click', async () => { const button = $('submit'); button.disabled = true; try { const done = await call('/api/v1/hide-error-demo/run',{run_id:runId,action:'submit'}); renderResult(done.result || done); $('status').textContent = 'Ответ сдан и проверен локальным оценщиком.'; } catch (error) { $('status').innerHTML = `<span class="error">${esc(error.message)}</span>`; button.disabled = false; } });
  $('batch-button').addEventListener('click', async () => { const button = $('batch-button'); button.disabled = true; button.textContent = 'Проверяем 10 компаний…'; try { const response = await call('/api/v1/hide-error-demo/batch',{case_id:state.case_view.case_id}); renderBatch(response.summary || response); button.textContent = 'Проверить ещё 10 компаний'; button.disabled = false; } catch (error) { $('batch-results').classList.remove('hidden'); $('batch-results').innerHTML = `<p class="error">${esc(error.message)}</p>`; button.textContent = 'Повторить проверку'; button.disabled = false; } });
})();
</script></body></html>'''
