/* Shared UI controls: theme toggle, fullscreen, active-tab tracking. Loaded on every page. */
(function () {
  const root = document.documentElement;
  const nav = document.querySelector(".binder__inner");
  if (!nav) return;

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage may be blocked */ } },
  };

  const icon = (paths) => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
  const SUN = icon('<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/>');
  const MOON = icon('<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>');
  const EXPAND = icon('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>');
  const SHRINK = icon('<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>');

  const box = document.createElement("div");
  box.className = "ui-ctrls";
  const brand = nav.querySelector(".brand");
  (brand ? brand.after(box) : nav.prepend(box));

  // ------------------------------------------------------------ theme
  const effective = () => root.getAttribute("data-theme") ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const themeBtn = document.createElement("button");
  themeBtn.type = "button";
  themeBtn.className = "ui-ctrl";
  function paintTheme() {
    const dark = effective() === "dark";
    themeBtn.innerHTML = dark ? SUN : MOON;
    themeBtn.setAttribute("aria-label", dark ? "Светлая тема" : "Тёмная тема");
    themeBtn.title = dark ? "Светлая тема" : "Тёмная тема";
  }
  themeBtn.addEventListener("click", () => {
    const next = effective() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    store.set("genesis-theme", next);
    paintTheme();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintTheme);
  paintTheme();
  box.append(themeBtn);

  // ------------------------------------------------------------ fullscreen
  if (document.fullscreenEnabled) {
    const fsBtn = document.createElement("button");
    fsBtn.type = "button";
    fsBtn.className = "ui-ctrl";
    const paintFs = () => {
      const on = !!document.fullscreenElement;
      fsBtn.innerHTML = on ? SHRINK : EXPAND;
      fsBtn.setAttribute("aria-label", on ? "Выйти из полноэкранного режима" : "На весь экран");
      fsBtn.title = on ? "Выйти из полноэкранного режима (Esc)" : "На весь экран";
      fsBtn.setAttribute("aria-pressed", String(on));
    };
    fsBtn.addEventListener("click", () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else root.requestFullscreen().catch(() => { /* blocked by the browser: nothing to do */ });
    });
    document.addEventListener("fullscreenchange", paintFs);
    paintFs();
    box.append(fsBtn);
  }

  // ------------------------------------------------------------ active tab
  // On the landing page the A–E tabs point at sections: highlight the one in view.
  const tabs = [...nav.querySelectorAll('a.tab[href^="#"]')];
  const sections = tabs.map((a) => document.querySelector(a.getAttribute("href"))).filter(Boolean);
  function activate(id) {
    tabs.forEach((a) => {
      const on = a.getAttribute("href") === "#" + id;
      a.classList.toggle("is-active", on);
      if (on) {
        a.setAttribute("aria-current", "location");
        // keep the active tab visible in the scrollable tab row on narrow screens
        const r = a.getBoundingClientRect(), n = nav.getBoundingClientRect();
        if (r.left < n.left || r.right > n.right) nav.scrollBy({ left: r.left - n.left - 80, behavior: "smooth" });
      } else {
        a.removeAttribute("aria-current");
      }
    });
  }
  if (sections.length && "IntersectionObserver" in window) {
    const visible = new Map();
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => visible.set(e.target.id, e.isIntersecting ? e.intersectionRatio : 0));
      const best = [...visible.entries()].filter(([, r]) => r > 0).sort((a, b) => b[1] - a[1])[0];
      if (best) activate(best[0]);
      else if (window.scrollY < 200) activate("");
    }, { rootMargin: "-80px 0px -40% 0px", threshold: [0, 0.1, 0.25, 0.5, 0.75] });
    sections.forEach((s) => io.observe(s));
    tabs.forEach((a) => a.addEventListener("click", () => activate(a.getAttribute("href").slice(1))));
  }
})();
