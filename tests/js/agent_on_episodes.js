// Runs frontend/agent.js on every open episode in the API record shape; prints findings as JSON lines.
// Used by tests/test_browser_agent.py (skipped when Node.js is not installed).
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = process.argv[2];
const ctx = { window: {}, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(root, "frontend", "agent.js"), "utf8"), ctx);
vm.runInContext(fs.readFileSync(path.join(root, "frontend", "data", "index.js"), "utf8"), ctx);
for (const e of ctx.window.GENESIS_INDEX) {
  vm.runInContext(fs.readFileSync(path.join(root, "frontend", "data", e.episode_id + ".js"), "utf8"), ctx);
  const ep = ctx.window.GENESIS_EPISODE;
  // Same shape as the environment adapter: ledger row_id -> id, invoices/payments keep doc_id and gain id.
  const t = {
    ledger: ep.tables.ledger.map(({ row_id, ...r }) => ({ id: row_id, ...r })),
    invoices: ep.tables.invoices.map((r) => ({ ...r, id: r.invoice_id })),
    payments: ep.tables.payments.map((r) => ({ ...r, id: r.payment_id })),
    fx: ep.tables.fx.map((r) => ({ ...r, id: `fx:${r.date}:${r.source_currency}:${r.target_currency}` })),
  };
  for (const name of ["simple", "careful"]) {
    const findings = ctx.window.Agents[name].findings(t, ep.manifest);
    console.log(JSON.stringify({ split: e.split, seed: e.seed, episode_id: e.episode_id, agent: name, findings }));
  }
}
