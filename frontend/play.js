/* Sandbox: a human (or a browser agent) plays one episode through the contract HTTP API. */
(function () {
  const { el, money, signed, plural, TYPE_RU } = window.G;
  const API = "/api/v1";
  const TIME_LIMIT = 60;
  const ID_RE = /^(ledger|invoice|payment|doc|fx|entity):\S+$/;
  const $ = (id) => document.getElementById(id);

  const S = {
    token: null, id: null, manifest: null, remaining: 100, startedAt: 0, finished: false, busy: false,
    cursors: {}, drafts: [], active: 0, steps: 0, timer: null,
  };

  // ------------------------------------------------------------ API
  async function api(path, options) {
    const res = await fetch(API + path, options);
    const type = res.headers.get("Content-Type") || "";
    const body = type.startsWith("application/json") ? await res.json() : await res.text();
    if (!res.ok) {
      const err = new Error((body && body.error && body.error.message) || "Сервер вернул " + res.status);
      err.code = body && body.error ? body.error.code : "http_" + res.status;
      throw err;
    }
    return body;
  }
  const authed = (extra) => ({ ...extra, headers: { ...(extra && extra.headers), Authorization: "Bearer " + S.token } });

  async function checkApi() {
    const status = $("api-status");
    if (location.protocol === "file:") {
      status.textContent = "Песочнице нужен сервер среды. Запустите из корня репозитория: make site (или PYTHONPATH=src:. python -m space_team_genesis.cli serve --mode open) и откройте http://127.0.0.1:8000/play.html.";
      $("create").disabled = true;
      $("create").dataset.noapi = "1";
      return;
    }
    try {
      const res = await fetch(API + "/episodes/ping");
      const json = (res.headers.get("Content-Type") || "").startsWith("application/json");
      if (!json) throw new Error();
      status.textContent = "Сервер среды отвечает. Seed и режим оценки выбирает он, не браузер.";
    } catch {
      status.textContent = "API не найден. Этот адрес отдаёт только статику: запустите make site и откройте сайт с его адреса.";
      $("create").disabled = true;
      $("create").dataset.noapi = "1";
    }
  }

  // ------------------------------------------------------------ formatting
  const dec = (c) => (S.manifest && S.manifest.currency_decimals[c] != null ? S.manifest.currency_decimals[c] : 2);
  function valueNode(key, value, row) {
    if (value === null) return el("span", { class: "null", text: "null" });
    if (typeof value === "string" && ID_RE.test(value)) {
      return el("button", { class: "idchip", type: "button", title: "Добавить в вывод", onclick: () => addToDraft(value), text: value });
    }
    if (/_minor$/.test(key) && typeof value === "number") {
      const c = (row && row.currency) || (S.manifest && S.manifest.base_currency);
      return el("span", { title: String(value), text: money(value, c, dec(c)) });
    }
    if (Array.isArray(value)) {
      if (value.every((v) => typeof v === "string")) {
        return el("span", {}, value.flatMap((v, i) => [i ? ", " : null, valueNode(key, v, row)]));
      }
      return el("span", { class: "small", text: JSON.stringify(value) });
    }
    if (typeof value === "object") return el("span", { class: "small", text: JSON.stringify(value) });
    return document.createTextNode(String(value));
  }

  function recordsTable(records) {
    if (!records.length) return el("p", { class: "muted", text: "Записей нет." });
    const keys = [...new Set(records.flatMap((r) => Object.keys(r)))].filter((k) => k !== "line_items");
    keys.sort((a, b) => (a === "id" ? -1 : b === "id" ? 1 : 0));
    return el("div", { class: "table-wrap" }, [el("table", { class: "grid-table" }, [
      el("thead", {}, [el("tr", {}, keys.map((k) => el("th", { scope: "col", text: k })))]),
      el("tbody", {}, records.map((r) => el("tr", {}, keys.map((k) => el("td", { class: /_minor$/.test(k) ? "num" : "" }, [valueNode(k, r[k], r)]))))),
    ])]);
  }

  function kvTable(obj) {
    const rows = Object.entries(obj).filter(([k]) => k !== "line_items").map(([k, v]) =>
      el("tr", {}, [el("th", { scope: "row", text: k }), el("td", {}, [valueNode(k, v, obj)])]));
    const nodes = [el("div", { class: "table-wrap" }, [el("table", { class: "kv" }, [el("tbody", {}, rows)])])];
    if (obj.line_items) {
      nodes.push(el("ul", { class: "small", style: "margin:10px 0 0" }, obj.line_items.map((li) =>
        el("li", { text: `${li.description}: ${li.quantity} × ${money(li.unit_price_minor, obj.currency, dec(obj.currency))} = ${money(li.amount_minor, obj.currency, dec(obj.currency))}` }))));
    }
    return nodes;
  }

  function renderObservation(obs) {
    const box = $("result");
    const head = (text) => el("p", { class: "result__head", text });
    switch (obs.kind) {
      case "records":
        S.cursors[obs.request.table] = obs.next_cursor;
        $("next-page").disabled = !obs.next_cursor;
        box.replaceChildren(head(`${obs.request.table}: ${obs.records.length} ${plural(obs.records.length, ["запись", "записи", "записей"])}` +
          (obs.next_cursor ? ", есть следующая страница" : ", это последняя страница")), recordsTable(obs.records));
        break;
      case "record":
        box.replaceChildren(head("Запись"), ...kvTable(obs.record));
        break;
      case "document":
        box.replaceChildren(head("Документ"), ...kvTable(obs.document));
        break;
      case "calculation":
        box.replaceChildren(head("Калькулятор"), el("p", { class: "calc-result", text: `${obs.expression} = ${obs.result}` }));
        break;
      case "submission":
        box.replaceChildren(head("Выводы приняты"), el("p", { text: `Принято выводов: ${obs.accepted_findings}.` }));
        break;
      case "error":
        box.replaceChildren(head("Ошибка действия (шаг потрачен)"), el("p", { class: "neg", text: `${obs.error.code}: ${obs.error.message}` }));
        break;
      case "truncated":
        box.replaceChildren(head("Эпизод обрезан"), el("p", { text: "Закончились шаги или время. Итоговый ответ считается пустым." }));
        break;
      default:
        box.replaceChildren(el("pre", { text: JSON.stringify(obs, null, 2) }));
    }
  }

  function describe(action) {
    switch (action.kind) {
      case "list_records": return `list_records ${action.table}${action.cursor ? " (дальше)" : ""}`;
      case "get_record": return `get_record ${action.id}`;
      case "get_document": return `get_document ${action.doc_id}`;
      case "calculator": return `calculator ${action.expression}`;
      case "submit": return `submit, выводов: ${action.findings.findings.length}`;
      default: return action.kind;
    }
  }
  function outcome(obs) {
    if (obs.kind === "records") return `${obs.records.length} строк`;
    if (obs.kind === "error") return obs.error.code;
    if (obs.kind === "submission") return "принято";
    if (obs.kind === "truncated") return "обрезано";
    return obs.kind;
  }

  // ------------------------------------------------------------ episode lifecycle
  async function create() {
    if (S.busy) return;
    setBusy(true);
    try {
      const body = JSON.stringify({ dataset_profile: $("profile").value, company_size: $("size").value });
      const res = await api("/episodes", { method: "POST", headers: { "Content-Type": "application/json" }, body });
      Object.assign(S, { token: res.episode_token, id: res.info.episode_id, manifest: res.observation.manifest,
        remaining: res.observation.remaining_steps, startedAt: performance.now(), finished: false, cursors: {}, steps: 0 });
      S.drafts = [];
      addDraft();
      const m = S.manifest;
      $("m-episode").textContent = S.id;
      $("m-company").textContent = `${m.timezone}, ${m.base_currency}, ${m.record_counts.ledger} проводок`;
      $("m-steps").textContent = S.remaining;
      $("log").replaceChildren();
      $("final").hidden = true;
      $("download").hidden = true;
      $("result").replaceChildren(el("p", { class: "muted", text: `Период ${m.period_start} … ${m.period_end}. Таймер уже идёт.` }));
      $("board").hidden = false;
      document.querySelectorAll(".action-form button, .autopilot button").forEach((b) => { b.disabled = false; });
      $("next-page").disabled = true;
      clearInterval(S.timer);
      S.timer = setInterval(tick, 250);
      tick();
      $("board").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    } catch (e) {
      $("api-status").textContent = `Не удалось создать эпизод: ${e.message}`;
    } finally {
      setBusy(false);
    }
  }

  function tick() {
    const left = Math.max(0, TIME_LIMIT - (performance.now() - S.startedAt) / 1000);
    $("m-time").textContent = S.finished ? "—" : `${Math.ceil(left)} с`;
    $("m-time").classList.toggle("neg", !S.finished && left < 10);
  }

  async function step(action) {
    if (S.finished) return null;
    try {
      const res = await api(`/episodes/${encodeURIComponent(S.id)}/steps`, authed({
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(action),
      }));
      S.steps = res.info.step;
      S.remaining = res.observation.remaining_steps;
      $("m-steps").textContent = S.remaining;
      renderObservation(res.observation);
      $("log").prepend(el("li", {}, [el("strong", { text: describe(action) }), " → ", outcome(res.observation)]));
      if (res.terminated || res.truncated) finish(res);
      return res;
    } catch (e) {
      if (e.code === "episode_finalized") finish(null);
      $("result").replaceChildren(el("p", { class: "neg", text: `${e.code}: ${e.message}` }));
      return null;
    }
  }

  function finish(res) {
    S.finished = true;
    clearInterval(S.timer);
    tick();
    document.querySelectorAll(".action-form button, .autopilot button").forEach((b) => { b.disabled = true; });
    const box = $("final");
    const parts = [];
    if (res) {
      const how = res.terminated ? "Эпизод завершён отправкой выводов." : "Эпизод обрезан по лимиту шагов или времени.";
      parts.push(el("p", { class: "final__head", text: how }));
      parts.push(el("p", { class: "final__reward" }, ["Награда: ", el("b", { class: res.reward < 0 ? "neg" : res.reward > 0 ? "pos" : "", text: signed(res.reward, 3) })]));
      parts.push(el("p", { class: "small muted", text: res.reward === 0 && res.terminated
        ? "Если сервер запущен в режиме closed, публичная награда всегда 0: оценка остаётся у оценщика. Для показа запускайте make site (режим open)."
        : `Шагов исследования: ${Math.max(0, S.steps - (res.terminated ? 1 : 0))}. Формула: clip(2·F1 − 1 − 0,002·шаги, −1, 1).` }));
    } else {
      parts.push(el("p", { class: "final__head", text: "Эпизод уже завершён." }));
    }
    parts.push(el("button", { class: "btn", type: "button", onclick: create, text: "Новый эпизод" }));
    box.replaceChildren(...parts);
    box.hidden = false;
    $("download").hidden = false;
  }

  async function download() {
    try {
      const res = await fetch(`${API}/episodes/${encodeURIComponent(S.id)}/trajectory`, authed({}));
      if (!res.ok) throw new Error(String(res.status));
      const blob = new Blob([await res.text()], { type: "application/x-ndjson" });
      const a = el("a", { href: URL.createObjectURL(blob), download: `${S.id}-trajectory.jsonl` });
      document.body.append(a);
      a.click();
      a.remove();
    } catch (e) {
      $("result").replaceChildren(el("p", { class: "neg", text: `Не удалось скачать траекторию: ${e.message}` }));
    }
  }

  // ------------------------------------------------------------ manual actions
  function showForm(action) {
    document.querySelectorAll("#action-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.action === action)));
    document.querySelectorAll(".action-form").forEach((f) => { f.hidden = f.dataset.form !== action; });
  }

  async function onSubmitForm(e) {
    e.preventDefault();
    if (S.busy || S.finished) return;
    const f = e.target;
    const kind = f.dataset.form;
    // f.elements: `f.id` would return the form's own id attribute, not the input named "id".
    const v = (name) => f.elements.namedItem(name).value;
    let action;
    if (kind === "list_records") {
      const table = v("table");
      const next = e.submitter && e.submitter.value === "next";
      action = { kind, table, cursor: next ? S.cursors[table] || null : null, limit: Number(v("limit")) || 50 };
    } else if (kind === "get_record") {
      action = { kind, table: v("table"), id: v("id").trim() };
    } else if (kind === "get_document") {
      action = { kind, doc_id: v("doc_id").trim() };
    } else if (kind === "calculator") {
      action = { kind, expression: v("expression") };
    } else {
      const findings = S.drafts.map(readDraft);
      const incomplete = findings.some((x) => !x.affected_ids.length || !x.evidence_ids.length || !Number.isInteger(x.claimed_amount_minor));
      if (incomplete && !confirm("Есть незаполненные поля. Некорректная структура завершит эпизод с наградой −1. Всё равно отправить?")) return;
      action = { kind: "submit", findings: { episode_id: S.id, findings } };
    }
    setBusy(true);
    await step(action);
    setBusy(false);
  }

  // ------------------------------------------------------------ findings drafts
  const splitIds = (s) => s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
  function readDraft(d) {
    return { type: d.type.value, affected_ids: [...new Set(splitIds(d.affected_ids.value))].sort(),
      claimed_amount_minor: Number(d.amount.value), currency: d.currency.value,
      evidence_ids: [...new Set(splitIds(d.evidence_ids.value))].sort(), explanation: d.explanation.value.trim() || "Найдено в открытых записях." };
  }

  function addDraft(prefill) {
    const n = S.drafts.length;
    const ccys = S.manifest ? Object.keys(S.manifest.currency_decimals) : ["KZT"];
    const field = (label, node) => el("label", {}, [label, node]);
    const d = {};
    d.type = el("select", {}, Object.entries(TYPE_RU).map(([v, t]) => el("option", { value: v, text: t })));
    d.affected_ids = el("input", { autocomplete: "off", placeholder: "invoice:… payment:… или ledger:…" });
    d.amount = el("input", { type: "number", step: "1", min: "0", placeholder: "в минимальных единицах, например 6887" });
    d.currency = el("select", {}, ccys.map((c) => el("option", { value: c, text: c })));
    d.evidence_ids = el("input", { autocomplete: "off", placeholder: "doc:… ledger:… fx:…" });
    d.explanation = el("input", { autocomplete: "off", placeholder: "Коротко: почему это ошибка" });
    if (S.manifest) d.currency.value = S.manifest.base_currency;
    const card = el("fieldset", { class: "draft" }, [
      el("legend", { text: `Вывод ${n + 1}` }),
      field("Тип", d.type), field("Затронутые записи", d.affected_ids), field("Сумма", d.amount),
      field("Валюта", d.currency), field("Доказательства", d.evidence_ids), field("Объяснение", d.explanation),
      el("button", { class: "linkbtn small", type: "button", text: "Удалить вывод", onclick: () => removeDraft(d) }),
    ]);
    d.card = card;
    card.addEventListener("focusin", () => setActive(S.drafts.indexOf(d)));
    card.addEventListener("click", () => setActive(S.drafts.indexOf(d)));
    if (prefill) {
      d.type.value = prefill.type;
      d.affected_ids.value = prefill.affected_ids.join(" ");
      d.amount.value = prefill.claimed_amount_minor;
      d.currency.value = prefill.currency;
      d.evidence_ids.value = prefill.evidence_ids.join(" ");
      d.explanation.value = prefill.explanation;
    }
    S.drafts.push(d);
    renderDrafts();
    setActive(S.drafts.length - 1);
  }
  function removeDraft(d) {
    S.drafts = S.drafts.filter((x) => x !== d);
    renderDrafts();
    setActive(Math.max(0, Math.min(S.active, S.drafts.length - 1)));
  }
  function renderDrafts() {
    $("drafts").replaceChildren(...(S.drafts.length ? S.drafts.map((d, i) => {
      d.card.querySelector("legend").textContent = `Вывод ${i + 1}`;
      return d.card;
    }) : [el("p", { class: "small muted", text: "Выводов нет: отправка пустого ответа тоже допустима, если книга чистая." })]));
  }
  function setActive(i) {
    S.active = i;
    S.drafts.forEach((d, k) => d.card.classList.toggle("is-active", k === i));
  }
  function addToDraft(id) {
    if (S.finished) return;
    if (!S.drafts.length) addDraft();
    const d = S.drafts[S.active] || S.drafts[0];
    const target = document.querySelector('input[name="target"]:checked').value;
    const input = target === "affected_ids" ? d.affected_ids : d.evidence_ids;
    const ids = new Set(splitIds(input.value));
    ids.add(id);
    input.value = [...ids].join(" ");
    input.classList.remove("flash");
    void input.offsetWidth;
    input.classList.add("flash");
  }

  // ------------------------------------------------------------ autopilot
  async function autopilot(key) {
    if (S.busy || S.finished) return;
    const agent = window.Agents[key];
    setBusy(true);
    const tables = { ledger: [], invoices: [], payments: [], fx: [] };
    try {
      for (const table of Object.keys(tables)) {
        let cursor = null;
        do {
          const res = await step({ kind: "list_records", table, cursor, limit: 50 });
          if (!res || res.observation.kind !== "records") return;
          tables[table].push(...res.observation.records);
          cursor = res.observation.next_cursor;
        } while (cursor);
      }
      const findings = agent.findings(tables, S.manifest);
      S.drafts = [];
      findings.forEach((f) => addDraft(f));
      renderDrafts(); // also when the agent found nothing (clean books)
      showForm("submit");
      await step({ kind: "submit", findings: { episode_id: S.id, findings } });
      $("log").prepend(el("li", { class: "small muted", text: `${agent.name}: ${agent.note}; отправлено выводов: ${findings.length}.` }));
    } finally {
      setBusy(false);
    }
  }

  function setBusy(v) {
    S.busy = v;
    document.body.classList.toggle("is-busy", v);
    $("create").disabled = v || $("create").dataset.noapi === "1";
  }

  // ------------------------------------------------------------ wiring
  $("create").addEventListener("click", create);
  $("download").addEventListener("click", download);
  $("add-draft").addEventListener("click", () => addDraft());
  document.querySelectorAll(".action-form").forEach((f) => f.addEventListener("submit", onSubmitForm));
  document.querySelectorAll("#action-tabs button").forEach((b) => b.addEventListener("click", () => showForm(b.dataset.action)));
  document.querySelectorAll("[data-agent]").forEach((b) => b.addEventListener("click", () => autopilot(b.dataset.agent)));

  // Demo links: play.html?profile=dev&size=small&auto=careful creates an episode and runs an agent.
  const q = new URLSearchParams(location.search);
  if (["train", "dev"].includes(q.get("profile"))) $("profile").value = q.get("profile");
  if (["small", "medium", "large"].includes(q.get("size"))) $("size").value = q.get("size");
  checkApi().then(async () => {
    const auto = q.get("auto");
    if ($("create").disabled || !(auto === "simple" || auto === "careful" || auto === "start")) return;
    await create();
    if (auto !== "start" && S.token) await autopilot(auto);
  });
})();
