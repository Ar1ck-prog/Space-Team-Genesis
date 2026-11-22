/* Landing page behaviour: hero reveal, reward calculator, data tables. */
(function () {
  const { el, signed, TYPE_RU, SCEN_RU, SIZE_RU } = window.G;

  // Hero: play the audit marks once, when the working paper is visible.
  const wp = document.getElementById("workpaper");
  if (wp) {
    const play = () => wp.classList.add("play");
    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) { play(); io.disconnect(); }
      }, { threshold: 0.4 });
      io.observe(wp);
    } else {
      play();
    }
  }

  // Reward calculator: the contract's formula and zero rules.
  const ids = ["tp", "fp", "fn", "steps"];
  const inputs = Object.fromEntries(ids.map((k) => [k, document.getElementById("i-" + k)]));
  function recalc() {
    const v = Object.fromEntries(ids.map((k) => [k, Number(inputs[k].value)]));
    ids.forEach((k) => { document.getElementById("o-" + k).textContent = v[k]; });
    const p = v.tp + v.fp ? v.tp / (v.tp + v.fp) : 0;
    const r = v.tp + v.fn ? v.tp / (v.tp + v.fn) : 0;
    const f1 = v.tp + v.fp + v.fn === 0 ? 1 : (2 * v.tp) / (2 * v.tp + v.fp + v.fn);
    const reward = Math.max(-1, Math.min(1, 2 * f1 - 1 - 0.002 * v.steps));
    document.getElementById("reward-value").textContent = signed(reward, 3);
    document.getElementById("pin").style.left = ((reward + 1) / 2) * 100 + "%";
    document.getElementById("m-p").textContent = "точность " + p.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
    document.getElementById("m-r").textContent = "полнота " + r.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
    document.getElementById("m-f1").textContent = "F1 " + f1.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
  }
  if (inputs.tp) {
    ids.forEach((k) => inputs[k].addEventListener("input", recalc));
    recalc();
  }

  // Strategy table, scored by the real evaluator at build time.
  const demo = window.GENESIS_DEMO;
  const tbody = document.querySelector("#strategies tbody");
  if (demo && tbody) {
    tbody.replaceChildren(...demo.rewards.map((r) => el("tr", {}, [
      el("td", {}, [el("strong", { text: r.name }), el("span", { class: "small muted", style: "display:block" }, r.why)]),
      el("td", { class: "num", text: r.steps }),
      el("td", { class: "num", text: r.f1.toLocaleString("ru-RU", { maximumFractionDigits: 3 }) }),
      el("td", { class: "num " + (r.reward < 0 ? "neg" : r.reward > 0.9 ? "pos" : ""), text: signed(r.reward, 3) }),
    ])));
  }

  // Episode table and stats.
  const index = window.GENESIS_INDEX;
  const etb = document.querySelector("#episode-table tbody");
  if (index && etb) {
    const clean = index.filter((e) => e.n_issues === 0).length;
    const inter = index.filter((e) => Object.keys(e.scenarios).length).length;
    const zones = new Set(index.map((e) => e.timezone)).size;
    const ccy = new Set(index.map((e) => e.base_currency)).size;
    const issues = index.reduce((s, e) => s + e.n_issues, 0);
    const P = window.G.plural;
    document.getElementById("stats").replaceChildren(
      ...[[issues, P(issues, ["скрытая ошибка", "скрытые ошибки", "скрытых ошибок"])],
          [clean, P(clean, ["чистая компания", "чистые компании", "чистых компаний"])],
          [inter, "с переплетёнными ошибками"],
          [zones, P(zones, ["часовой пояс", "часовых пояса", "часовых поясов"])],
          [ccy, P(ccy, ["базовая валюта", "базовые валюты", "базовых валют"])]]
        .map(([n, t]) => el("li", {}, [el("b", { text: n }), t]))
    );
    etb.replaceChildren(...index.map((e) => {
      const types = Object.entries(e.issue_types).map(([t, n]) => el("span", { class: "pill", text: `${TYPE_RU[t]} ×${n}` }));
      const scen = Object.keys(e.scenarios).map((s) => el("span", { class: "pill pill--scen", text: SCEN_RU[s] }));
      return el("tr", {}, [
        el("td", {}, [e.split, el("span", { class: "small muted", style: "display:block" }, "seed " + e.seed)]),
        el("td", {}, [el("a", { href: "explorer.html?ep=" + encodeURIComponent(e.episode_id), text: e.description })]),
        el("td", {}, [SIZE_RU[e.size] + ", " + e.base_currency,
          el("span", { class: "small muted", style: "display:block" }, e.timezone + " " + e.period_start.slice(-6))]),
        el("td", { class: "num", text: e.record_counts.ledger }),
        el("td", {}, types.length ? [...types, ...scen] : [el("span", { class: "muted", text: "нет, книга чистая" })]),
      ]);
    }));
  }
})();
