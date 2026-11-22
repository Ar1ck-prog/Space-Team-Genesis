/* Episode explorer: public records as the agent sees them, 50 rows per page. */
(function () {
  const { el, money, TYPE_RU, SCEN_RU, SIZE_RU } = window.G;
  const INDEX = window.GENESIS_INDEX || [];
  const DEMO = window.GENESIS_DEMO || {};
  const PAGE = 50;

  const state = { ep: null, meta: null, table: "ledger", query: "", page: 0, selected: null, hits: null, hitLabel: "" };
  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- time helpers
  function offsetMinutes(iso) {
    const m = iso.match(/([+-])(\d{2}):(\d{2})$/);
    return m ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])) : 0;
  }
  function inCompanyTime(iso) {
    const company = offsetMinutes(state.ep.manifest.period_start);
    const d = new Date(Date.parse(iso) + company * 60000);
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getUTCDate())}.${p(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}, ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  }
  function tsCell(iso) {
    const printed = iso.replace("T", " ").slice(0, 16) + " " + (iso.endsWith("+00:00") ? "UTC" : iso.slice(-6));
    const sameOffset = iso.slice(-6) === state.ep.manifest.period_start.slice(-6);
    return el("td", { class: "ts" }, [printed, sameOffset ? null : el("span", { class: "ts-local", text: "по времени компании: " + inCompanyTime(iso) })]);
  }

  // ---------------------------------------------------------------- formatting
  const dec = (c) => state.ep.manifest.currency_decimals[c] ?? 2;
  const amt = (minor, c) => money(minor, c, dec(c));
  const nul = () => el("span", { class: "null", text: "null" });
  const idc = (v) => (v == null ? el("td", {}, [nul()]) : el("td", { class: "idcell", text: v }));
  const fxId = (r) => `fx:${r.date}:${r.source_currency}:${r.target_currency}`;

  const COLUMNS = {
    ledger: {
      head: ["Строка", "Время", "Счёт", "Дебет", "Кредит", "Контрагент", "Счёт-фактура", "Платёж"],
      num: [3, 4],
      row: (r) => [
        idc(r.row_id), tsCell(r.timestamp),
        el("td", {}, [r.account, el("span", { class: "small muted", style: "display:block" }, state.ep.policy.accounts?.[r.account] || "")]),
        el("td", { class: "num", text: r.debit_minor ? amt(r.debit_minor, r.currency) : "" }),
        el("td", { class: "num", text: r.credit_minor ? amt(r.credit_minor, r.currency) : "" }),
        idc(r.entity_id), idc(r.invoice_id), idc(r.payment_id),
      ],
      ids: (r) => [r.row_id],
    },
    invoices: {
      head: ["Счёт", "Документ", "Контрагент", "Выставлен", "Срок", "Сумма", "Позиций"],
      num: [5, 6],
      row: (r) => [idc(r.invoice_id), idc(r.doc_id), idc(r.entity_id), tsCell(r.issued_at), tsCell(r.due_at),
        el("td", { class: "num", text: amt(r.total_minor, r.currency) }), el("td", { class: "num", text: r.line_items.length })],
      ids: (r) => [r.invoice_id, r.doc_id],
    },
    payments: {
      head: ["Платёж", "Документ", "Время", "Сумма", "Погашает счета"],
      num: [3],
      row: (r) => [idc(r.payment_id), idc(r.doc_id), tsCell(r.timestamp),
        el("td", { class: "num " + (r.amount_minor < 0 ? "neg" : ""), text: amt(r.amount_minor, r.currency) }),
        el("td", {}, r.invoice_refs ? r.invoice_refs.join(", ") : [nul()])],
      ids: (r) => [r.payment_id, r.doc_id],
    },
    fx: {
      head: ["Дата", "Пара", "Курс", "ID для доказательств"],
      num: [2],
      row: (r) => [el("td", { text: r.date }), el("td", { text: `${r.source_currency} → ${r.target_currency}` }),
        el("td", { class: "num", text: r.rate }), idc(fxId(r))],
      ids: (r) => [fxId(r)],
    },
  };

  // ---------------------------------------------------------------- episode list
  function renderList() {
    const list = $("ep-list");
    const items = [];
    for (const split of ["train", "dev"]) {
      items.push(el("li", { class: "ep-split", text: split }));
      for (const e of INDEX.filter((x) => x.split === split)) {
        const label = e.n_issues ? `${e.n_issues} ош.` : "чистая";
        items.push(el("li", {}, [el("button", {
          type: "button", "aria-pressed": String(state.meta && state.meta.episode_id === e.episode_id),
          onclick: () => load(e.episode_id, true),
        }, [e.description, el("span", { class: "ep-sub", text: `seed ${e.seed}, ${SIZE_RU[e.size]}, ${e.base_currency}, ${label}` })])]));
      }
    }
    list.replaceChildren(...items);
  }

  function renderFacts() {
    const m = state.ep.manifest;
    const pairs = [
      ["Набор", `${state.meta.split}, seed ${state.meta.seed}`],
      ["Период", `${m.period_start.slice(0, 10)} … ${m.period_end.slice(0, 10)}`],
      ["Часовой пояс", `${m.timezone}, UTC${m.period_start.slice(-6).replace("-", "−")}`],
      ["Базовая валюта", m.base_currency],
      ["Валюты", Object.keys(m.currency_decimals).join(", ")],
      ["Записей", `${m.record_counts.ledger} проводок, ${m.document_count} документов`],
      ["Курс берётся", "на местную дату документа"],
      ["Округление", "до копейки, половина вверх"],
    ];
    $("facts").replaceChildren(...pairs.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]));
  }

  function renderReview() {
    const box = $("review");
    if (state.meta.episode_id !== DEMO.demo_episode_id || !DEMO.walkthrough) { box.hidden = true; return; }
    box.hidden = false;
    $("review-list").replaceChildren(...DEMO.walkthrough.map((w) => {
      const scen = w.scenario ? `, сценарий «${SCEN_RU[w.scenario]}»` : "";
      return el("li", {}, [el("button", {
        type: "button",
        onclick: () => showHits(new Set([...w.affected_ids, ...w.evidence_ids]), `${TYPE_RU[w.type]}: ${amt(w.amount_minor, w.currency)}`),
      }, [el("strong", { text: `${TYPE_RU[w.type]}, ${amt(w.amount_minor, w.currency)}` }), scen, el("span", { class: "muted", style: "display:block" }, w.note)])]);
    }));
  }

  // ---------------------------------------------------------------- table
  function rowsFor(table) {
    let rows = state.ep.tables[table];
    if (state.hits) rows = rows.filter((r) => matchesHits(table, r));
    if (state.query) {
      const q = state.query.toLowerCase();
      rows = rows.filter((r) => JSON.stringify(r).toLowerCase().includes(q));
    }
    return rows;
  }
  function matchesHits(table, r) {
    if (!state.hits) return false;
    if (COLUMNS[table].ids(r).some((x) => state.hits.has(x))) return true;
    if (table === "ledger") return false;
    return false;
  }

  function renderTable() {
    const cfg = COLUMNS[state.table];
    const rows = rowsFor(state.table);
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    state.page = Math.min(state.page, pages - 1);
    const slice = rows.slice(state.page * PAGE, state.page * PAGE + PAGE);
    $("grid").querySelector("thead").replaceChildren(el("tr", {}, cfg.head.map((h, i) =>
      el("th", { class: cfg.num.includes(i) ? "num" : "", scope: "col", text: h }))));
    const body = slice.length ? slice.map((r) => {
      const key = cfg.ids(r)[0];
      const tr = el("tr", { tabindex: "0", onclick: () => select(state.table, r), onkeydown: (e) => { if (e.key === "Enter") select(state.table, r); } }, cfg.row(r));
      if (matchesHits(state.table, r)) tr.classList.add("is-hit");
      if (state.selected === key) tr.classList.add("is-selected");
      return tr;
    }) : [el("tr", {}, [el("td", { class: "empty", colspan: cfg.head.length, text: state.hits ? "В этой таблице нет записей выбранной ошибки. Переключите таблицу или сбросьте фильтр." : "Ничего не найдено. Проверьте запрос или очистите поиск." })])];
    $("grid").querySelector("tbody").replaceChildren(...body);
    $("page-info").textContent = `страница ${state.page + 1} из ${pages}, записей ${rows.length}`;
    $("prev").disabled = state.page === 0;
    $("next").disabled = state.page >= pages - 1;
    document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.table === state.table)));
    const note = $("filter-note");
    if (state.hits) {
      note.hidden = false;
      note.replaceChildren(`Показаны записи ошибки «${state.hitLabel}». `, el("button", { class: "linkbtn", type: "button", onclick: clearHits, text: "Показать все записи" }));
    } else {
      note.hidden = true;
    }
  }

  function showHits(set, label) {
    state.hits = set;
    state.hitLabel = label;
    state.query = "";
    $("search").value = "";
    state.page = 0;
    state.table = "ledger";
    renderTable();
  }
  function clearHits() { state.hits = null; state.page = 0; renderTable(); }

  // ---------------------------------------------------------------- detail
  function jump(table, id) {
    state.hits = null;
    state.table = table;
    state.query = id;
    $("search").value = id;
    state.page = 0;
    const r = state.ep.tables[table].find((x) => COLUMNS[table].ids(x).includes(id));
    if (r) select(table, r); else renderTable();
  }
  const linkTo = (table, id) => el("button", { class: "linkbtn", type: "button", onclick: () => jump(table, id), text: id });

  function select(table, r) {
    state.selected = COLUMNS[table].ids(r)[0];
    state.table = table;
    renderTable();
    const t = state.ep.tables;
    const links = [];
    if (table === "ledger") {
      if (r.invoice_id) links.push(["Счёт", [linkTo("invoices", r.invoice_id)]]);
      if (r.payment_id) links.push(["Платёж", [linkTo("payments", r.payment_id)]]);
      const sib = t.ledger.filter((x) => x !== r && ((r.payment_id && x.payment_id === r.payment_id) || (!r.payment_id && r.invoice_id && !x.payment_id && x.invoice_id === r.invoice_id)));
      if (sib.length) links.push(["Та же проводка", sib.map((x) => linkTo("ledger", x.row_id))]);
    } else if (table === "invoices") {
      const posted = t.ledger.filter((x) => x.invoice_id === r.invoice_id);
      links.push(["Строки книги", posted.length ? posted.map((x) => linkTo("ledger", x.row_id)) : ["не проведён в этом периоде"]]);
      const pays = t.payments.filter((p) => (p.invoice_refs || []).includes(r.invoice_id));
      if (pays.length) links.push(["Платежи по счёту", pays.map((p) => linkTo("payments", p.payment_id))]);
    } else if (table === "payments") {
      const posted = t.ledger.filter((x) => x.payment_id === r.payment_id);
      links.push(["Строки книги", posted.length ? posted.map((x) => linkTo("ledger", x.row_id)) : ["не проведён в этом периоде"]]);
      if (r.invoice_refs) links.push(["Счета", r.invoice_refs.map((i) => linkTo("invoices", i))]);
    }
    const fields = Object.entries(r).filter(([k]) => k !== "line_items").map(([k, v]) => `${k}: ${v === null ? "null" : v}`).join("\n");
    const items = r.line_items ? r.line_items.map((li) => `${li.description}: ${li.quantity} × ${amt(li.unit_price_minor, r.currency)} = ${amt(li.amount_minor, r.currency)}`).join("\n") : "";
    const d = $("detail");
    d.hidden = false;
    d.replaceChildren(
      el("h3", { text: "Запись " + state.selected }),
      el("pre", { text: fields }),
      items ? el("pre", { text: items }) : null,
      ...links.map(([k, v]) => el("p", { class: "small", style: "margin:0 0 6px" }, [el("strong", { text: k + ": " }), ...v.flatMap((x, i) => (i ? [", ", x] : [x]))])),
    );
  }

  // ---------------------------------------------------------------- loading
  function load(id, push) {
    const meta = INDEX.find((e) => e.episode_id === id) || INDEX.find((e) => e.episode_id === DEMO.demo_episode_id) || INDEX[0];
    if (!meta) return;
    const s = document.createElement("script");
    s.src = "data/" + meta.episode_id + ".js";
    s.onload = () => {
      state.ep = window.GENESIS_EPISODE;
      state.meta = meta;
      Object.assign(state, { table: "ledger", query: "", page: 0, selected: null, hits: null });
      $("search").value = "";
      $("detail").hidden = true;
      $("ep-title").textContent = meta.description[0].toUpperCase() + meta.description.slice(1);
      $("ep-sub").textContent = `${meta.episode_id}. Ровно те записи, которые видит агент: эталона здесь нет.`;
      if (push) history.replaceState(null, "", "?ep=" + encodeURIComponent(meta.episode_id));
      renderList(); renderFacts(); renderReview(); renderTable();
      s.remove();
    };
    s.onerror = () => { $("ep-sub").textContent = "Не удалось загрузить данные эпизода. Пересоберите их командой python frontend/build_data.py."; };
    document.body.append(s);
  }

  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
    state.table = b.dataset.table; state.page = 0; renderTable();
  }));
  $("tabs").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const order = ["ledger", "invoices", "payments", "fx"];
    const i = order.indexOf(state.table) + (e.key === "ArrowRight" ? 1 : -1);
    state.table = order[(i + order.length) % order.length]; state.page = 0; renderTable();
    document.querySelector(`#tabs button[data-table="${state.table}"]`).focus();
  });
  let timer;
  $("search").addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.query = e.target.value.trim(); state.page = 0; renderTable(); }, 150);
  });
  $("prev").addEventListener("click", () => { state.page -= 1; renderTable(); });
  $("next").addEventListener("click", () => { state.page += 1; renderTable(); });

  load(new URLSearchParams(location.search).get("ep") || DEMO.demo_episode_id, false);
})();
