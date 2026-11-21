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

  // Results: strategies on the −1 … +1 reward scale, plus the full metrics table.
  const ladder = document.getElementById("ladder");
  const ptb = document.querySelector("#policy-table tbody");
  if (demo && demo.policies && ladder && ptb) {
    const fmt = (x, d) => x.toLocaleString("ru-RU", { minimumFractionDigits: d, maximumFractionDigits: d });
    const rungs = demo.policies.map((p) => {
      const v = p.return;
      const w = (Math.abs(v) / 2) * 100;
      const bar = el("div", { class: "rung__bar " + (v < 0 ? "neg" : "pos"),
        style: v < 0 ? `left:${50 - w}%;width:${w}%` : `left:50%;width:${w}%` });
      const val = el("div", { class: "rung__val " + (v < 0 ? "neg" : "pos"), text: signed(v, 3),
        style: v < 0 ? `right:calc(${50 + w}% + 8px)` : `left:calc(${50 + w}% + 8px)` });
      // Bars that reach the edge carry their value inside, in white, so it never leaves the track.
      if (v >= 0 && 50 + w > 86) { val.style.left = ""; val.style.right = `calc(${50 - w}% + 8px)`; val.style.color = "#fff"; }
      if (v < 0 && 50 - w < 14) { val.style.right = ""; val.style.left = `calc(${50 - w}% + 8px)`; val.style.color = "#fff"; }
      return el("div", { class: "rung", role: "listitem", "aria-label": `${p.name}: награда ${signed(v, 3)}` }, [
        el("div", { class: "rung__name" }, [p.name, el("span", { text: p.note })]),
        el("div", { class: "rung__track" }, [bar, val]),
      ]);
    });
    ladder.replaceChildren(...rungs, el("div", { class: "ladder__axis", "aria-hidden": "true" },
      [el("span", { text: "−1" }), el("span", { text: "0" }), el("span", { text: "+1" })]));
    ptb.replaceChildren(...demo.policies.map((p) => el("tr", {}, [
      el("td", {}, [el("strong", { text: p.name }), el("span", { class: "small muted", style: "display:block" }, p.note)]),
      el("td", { class: "num", text: fmt(p.precision, 3) }),
      el("td", { class: "num", text: fmt(p.recall, 3) }),
      el("td", { class: "num", text: fmt(p.f1, 3) }),
      el("td", { class: "num", text: p.false_positives }),
      el("td", { class: "num", text: fmt(p.steps, 1) }),
      el("td", { class: "num " + (p.return < 0 ? "neg" : "pos"), text: signed(p.return, 3) }),
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
