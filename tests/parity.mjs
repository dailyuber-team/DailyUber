// Calcule avec web/engine.js les cas préparés par tests/test_parity.py et écrit les résultats
// en JSON sur la sortie standard : node tests/parity.mjs data.json cases.json
import { readFileSync } from "node:fs";
import { pyRound, selectDeals } from "../web/engine.js";

const [dataPath, casesPath] = process.argv.slice(2);
const data = JSON.parse(readFileSync(dataPath, "utf8"));
const { cases, rounding } = JSON.parse(readFileSync(casesPath, "utf8"));

const deals = cases.map((c) => {
  const cfg = structuredClone(data.config);
  cfg.fees.uber_one.enabled = c.uber_one;
  cfg.selection.max_price_per_person = c.budget;
  const baskets = selectDeals(data.stores, cfg, {
    people: c.people,
    whenMs: c.when ? Date.parse(c.when) : null,
    nowMs: Date.parse(data.fetched_at),
    mealTypes: c.meal_types,
    topN: c.top_n,
    sort: c.sort,
  });
  return baskets.map((b) => ({
    uuid: b.store.uuid,
    lines: b.lines.map(([it, q]) => [it.title, q]),
    items_subtotal: b.items_subtotal, threshold_discount: b.threshold_discount,
    delivery_fee: b.delivery_fee, service_fee: b.service_fee, small_order_fee: b.small_order_fee,
    total: b.total, per_person: b.per_person, score: b.score,
    promos: b.promos_used, availability: b.availability,
  }));
});

const rounded = rounding.map(([x, n]) => pyRound(x, n));
process.stdout.write(JSON.stringify({ deals, rounded }));
