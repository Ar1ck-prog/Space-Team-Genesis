/* Browser agents for the sandbox. They see exactly what any agent sees: public records
   returned by list_records through the HTTP API. No truth, no seed.

   simple  - "simple reconciliation": only duplicate invoices (same as the baseline policy).
   careful - applies policy_summary to all four issue types (port of auditgym/books.py audit()).
*/
(function () {
  // ------------------------------------------------------------ exact money arithmetic
  function decimalToFraction(s) {
    const [i, f = ""] = String(s).split(".");
    return { num: BigInt(i + f), scale: f.length };
  }
  const pow10 = (n) => 10n ** BigInt(n);

  // round_half_up(amount * rate * 10^(exp(target) - exp(source))), exactly like auditgym/money.py
  function convertMinor(amount, source, target, rate, decimals) {
    if (source === target) return amount;
    const { num, scale } = decimalToFraction(rate);
    const d = decimals[target] - decimals[source];
    let n = BigInt(amount) * num * pow10(Math.max(0, d));
    const den = pow10(scale) * pow10(Math.max(0, -d));
    const neg = n < 0n;
    if (neg) n = -n;
    const q = (2n * n + den) / (2n * den); // half away from zero
    return Number(neg ? -q : q);
  }

  // ------------------------------------------------------------ helpers
  function offsetMinutes(iso) {
    const m = iso.match(/([+-])(\d{2}):(\d{2})$/);
    return m ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])) : 0;
  }
  const uniqSorted = (xs) => [...new Set(xs.filter(Boolean))].sort();

  function books(t, manifest) {
    const base = manifest.base_currency;
    const decimals = manifest.currency_decimals;
    const off = offsetMinutes(manifest.period_start);
    const start = Date.parse(manifest.period_start);
    const end = Date.parse(manifest.period_end);
    const rates = new Map(t.fx.filter((r) => r.target_currency === base).map((r) => [r.source_currency + "|" + r.date, r.rate]));
    const rowsByInvoice = new Map();
    const rowsByPayment = new Map();
    for (const r of t.ledger) {
      if (r.payment_id) (rowsByPayment.get(r.payment_id) || rowsByPayment.set(r.payment_id, []).get(r.payment_id)).push(r);
      else if (r.invoice_id) (rowsByInvoice.get(r.invoice_id) || rowsByInvoice.set(r.invoice_id, []).get(r.invoice_id)).push(r);
    }
    return {
      base, decimals, rowsByInvoice, rowsByPayment,
      invoices: new Map(t.invoices.map((d) => [d.invoice_id, d])),
      payments: new Map(t.payments.map((d) => [d.payment_id, d])),
      localDate: (ts) => new Date(Date.parse(ts) + off * 60000).toISOString().slice(0, 10),
      inPeriod: (ts) => { const x = Date.parse(ts); return start <= x && x <= end; },
      toBase(amount, ccy, date) {
        if (ccy === base) return amount;
        const r = rates.get(ccy + "|" + date);
        return r == null ? null : convertMinor(amount, ccy, base, r, decimals);
      },
      fxId: (ccy, date) => `fx:${date}:${ccy}:${base}`,
      rows: (map, key, account) => (map.get(key) || []).filter((r) => r.account === account),
    };
  }

  function finding(type, affected, amount, base, evidence, why) {
    return { type, affected_ids: uniqSorted(affected), claimed_amount_minor: amount, currency: base,
      evidence_ids: uniqSorted(evidence).slice(0, 10), explanation: why.slice(0, 1000) };
  }

  function duplicates(b, t) {
    const out = [];
    const groups = new Map();
    for (const inv of t.invoices) {
      if (!b.rows(b.rowsByInvoice, inv.invoice_id, "1200").length) continue;
      const key = [inv.entity_id, inv.currency, inv.total_minor, JSON.stringify(inv.line_items), b.localDate(inv.issued_at)].join("|");
      (groups.get(key) || groups.set(key, []).get(key)).push(inv);
    }
    for (const invs of groups.values()) {
      if (invs.length < 2) continue;
      invs.sort((x, y) => Date.parse(x.issued_at) - Date.parse(y.issued_at));
      for (const dup of invs.slice(1)) {
        const ar = b.rows(b.rowsByInvoice, dup.invoice_id, "1200")[0];
        out.push(finding("duplicate_invoice", [invs[0].invoice_id, dup.invoice_id], ar.debit_minor, b.base,
          [invs[0].doc_id, dup.doc_id, ar.id], "Тот же клиент, позиции, сумма и местный день проведены дважды."));
      }
    }
    return out;
  }

  // ------------------------------------------------------------ agents
  function simple(t, manifest) {
    return duplicates(books(t, manifest), t).slice(0, 30);
  }

  function careful(t, manifest) {
    const b = books(t, manifest);
    const out = [];
    for (const inv of t.invoices) {
      const ar = b.rows(b.rowsByInvoice, inv.invoice_id, "1200")[0];
      if (!ar) continue;
      const rev = b.rows(b.rowsByInvoice, inv.invoice_id, "4000")[0];
      if (!b.inPeriod(inv.issued_at)) {
        out.push(finding("reporting_period", b.rowsByInvoice.get(inv.invoice_id).map((r) => r.id), ar.debit_minor, b.base,
          [inv.doc_id, ar.id], "Счёт выставлен вне периода, но проведён в нём."));
        continue;
      }
      if (inv.currency === b.base) continue;
      const d = b.localDate(inv.issued_at);
      const expected = b.toBase(inv.total_minor, inv.currency, d);
      if (expected != null && expected !== ar.debit_minor) {
        out.push(finding("fx_date_or_rate", [ar.id, rev.id], Math.abs(ar.debit_minor - expected), b.base,
          [inv.doc_id, ar.id, b.fxId(inv.currency, d)], `Проведено ${ar.debit_minor}, по курсу политики ${expected}.`));
      }
    }
    for (const p of t.payments) {
      const rows = b.rowsByPayment.get(p.payment_id);
      if (!rows || p.amount_minor < 0) continue;
      const cash = b.rows(b.rowsByPayment, p.payment_id, "1000")[0];
      if (!b.inPeriod(p.timestamp)) {
        out.push(finding("reporting_period", rows.map((r) => r.id), cash.debit_minor, b.base, [p.doc_id, cash.id],
          "Платёж получен вне периода, но проведён в нём."));
        continue;
      }
      if (p.currency === b.base) continue;
      const d = b.localDate(p.timestamp);
      const expected = b.toBase(p.amount_minor, p.currency, d);
      if (expected != null && expected !== cash.debit_minor) {
        out.push(finding("fx_date_or_rate", [cash.id], Math.abs(cash.debit_minor - expected), b.base,
          [p.doc_id, cash.id, b.fxId(p.currency, d)], `В кассе ${cash.debit_minor}, по курсу политики ${expected}.`));
      }
    }
    out.push(...duplicates(b, t));
    // Allocation: replay "invoice_refs in order, each up to its outstanding balance" in posting order.
    const outstanding = new Map(t.invoices.map((d) => [d.invoice_id, d.total_minor]));
    const posted = t.payments.filter((p) => b.rowsByPayment.has(p.payment_id))
      .sort((x, y) => Date.parse(b.rowsByPayment.get(x.payment_id)[0].timestamp) - Date.parse(b.rowsByPayment.get(y.payment_id)[0].timestamp));
    for (const p of posted) {
      if (p.amount_minor < 0 || !p.invoice_refs) continue;
      let remaining = p.amount_minor;
      const unapplied = b.rows(b.rowsByPayment, p.payment_id, "2400")[0];
      for (const iid of p.invoice_refs) {
        const inv = b.invoices.get(iid);
        if (!inv) continue;
        const a = Math.min(remaining, outstanding.get(iid));
        if (a <= 0) continue;
        outstanding.set(iid, outstanding.get(iid) - a);
        remaining -= a;
        const expected = b.toBase(a, inv.currency, b.localDate(inv.issued_at));
        const actual = b.rows(b.rowsByPayment, p.payment_id, "1200").filter((r) => r.invoice_id === iid)
          .reduce((s, r) => s + r.credit_minor, 0);
        if (expected != null && expected > actual) {
          out.push(finding("payment_allocation", [p.payment_id, iid], expected - actual, b.base,
            [p.doc_id, inv.doc_id, unapplied && unapplied.id], `Ожидалось погашение ${expected}, проведено ${actual}.`));
        }
      }
    }
    return out.slice(0, 30);
  }

  window.Agents = {
    simple: { name: "Простая сверка", note: "ищет только дубли счетов", findings: simple },
    careful: { name: "Внимательный аудитор", note: "применяет политику ко всем четырём типам ошибок", findings: careful },
    convertMinor,
  };
})();
