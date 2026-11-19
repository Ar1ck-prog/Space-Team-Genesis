/* Shared formatting helpers. */
(function () {
  const nf = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });

  function money(minor, currency, decimals) {
    const d = decimals == null ? 2 : decimals;
    const v = minor / Math.pow(10, d);
    return new Intl.NumberFormat("ru-RU", { minimumFractionDigits: d, maximumFractionDigits: d }).format(v) + " " + currency;
  }

  function signed(x, digits) {
    const s = x.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    return x > 0 ? "+" + s : s.replace("-", "−");
  }

  function el(tag, attrs, children) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const c of [].concat(children || [])) {
      if (c == null) continue;
      n.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return n;
  }

  const TYPE_RU = {
    duplicate_invoice: "дубль счёта",
    payment_allocation: "распределение платежа",
    fx_date_or_rate: "курс или дата курса",
    reporting_period: "отчётный период",
  };
  const SCEN_RU = {
    fx_invoice_partial: "курс + частичное погашение",
    boundary_payment_fx_partial: "платёж на границе периода",
  };
  const SIZE_RU = { small: "малая", medium: "средняя", large: "крупная" };

  // Russian plural: plural(5, ["ошибка", "ошибки", "ошибок"]) -> "ошибок"
  function plural(n, forms) {
    const a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return forms[2];
    if (b === 1) return forms[0];
    if (b >= 2 && b <= 4) return forms[1];
    return forms[2];
  }

  // ?theme=light|dark forces a theme (handy on projectors); otherwise the system setting wins.
  const theme = new URLSearchParams(location.search).get("theme");
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);

  window.G = { nf, money, signed, el, plural, TYPE_RU, SCEN_RU, SIZE_RU };
})();
