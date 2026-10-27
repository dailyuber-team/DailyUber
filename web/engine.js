// Calcul des offres dans le téléphone, à partir de data.json (dailyuber/export.py).
//
// Portage fidèle de dailyuber/selection.py (paniers, frais, promos, score, doublons) et de
// hours.availability. Toute modification de ces fonctions Python doit être reportée ici :
// tests/test_parity.py vérifie que Python et JavaScript donnent exactement les mêmes offres.

const MAX_CANDIDATES = 40; // plats les moins chers considérés par restaurant (plus tous les 1+1)
const NOW_MARGIN_MIN = 30; // une heure visée à moins de 30 min est traitée comme "maintenant"

// round() de Python : arrondi sur la valeur binaire exacte, égalité vers le chiffre pair.
// (Math.round et toFixed arrondissent autrement, ce qui décalerait des centimes.)
export function pyRound(x, n = 0) {
  if (x === 0 || !Number.isFinite(x)) return x;
  // Cas courant, rapide : loin d'une égalité (…,5), l'erreur du produit (< 1e-9 ici) ne peut
  // pas changer le côté de l'arrondi, et k / 10^n donne le même double que Python.
  const p = 10 ** n, y = Math.abs(x) * p;
  if (y < 1e9) {
    const f = y - Math.floor(y);
    if (Math.abs(f - 0.5) > 1e-7) {
      const r = (f < 0.5 ? Math.floor(y) : Math.floor(y) + 1) / p;
      return x < 0 ? -r : r;
    }
  }
  const [int, frac] = Math.abs(x).toFixed(100).split("."); // décimales exactes du double
  const kept = int + frac.slice(0, n);
  const rest = frac.slice(n);
  let up = rest[0] > "5";
  if (rest[0] === "5") up = /[1-9]/.test(rest.slice(1)) || Number(kept[kept.length - 1]) % 2 === 1;
  const r = Number(BigInt(kept) + (up ? 1n : 0n)) / 10 ** n;
  return x < 0 ? -r : r;
}

// --- Heure de Paris -------------------------------------------------------------------------

const PARIS_FMT = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/Paris", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", weekday: "short", hourCycle: "h23",
});
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Date "AAAA-MM-JJ", minute depuis minuit et jour de la semaine (0 = lundi) à Paris. */
export function parisParts(ms) {
  const p = {};
  for (const { type, value } of PARIS_FMT.formatToParts(new Date(ms))) p[type] = value;
  return {
    date: `${p.year}-${p.month}-${p.day}`,
    minute: Number(p.hour) * 60 + Number(p.minute),
    weekday: WEEKDAYS.indexOf(p.weekday),
  };
}

/** Instant (ms) correspondant à une date et une minute affichées à Paris. */
export function parisToMs(date, minute) {
  const [y, m, d] = date.split("-").map(Number);
  const wall = Date.UTC(y, m - 1, d, Math.floor(minute / 60), minute % 60);
  const offset = (ms) => {
    const p = parisParts(ms);
    const [py, pm, pd] = p.date.split("-").map(Number);
    return Date.UTC(py, pm - 1, pd, Math.floor(p.minute / 60), p.minute % 60) - Math.floor(ms / 60000) * 60000;
  };
  let ms = wall - offset(wall);
  ms = wall - offset(ms); // second passage : décalage exact autour des changements d'heure
  return ms;
}

export function hhmm(minutes) {
  const m = ((minutes % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

function isOpenAt(weekly, parts) {
  return (weekly[parts.weekday] || []).some(([start, end]) => start <= parts.minute && parts.minute < end);
}

/** hours.availability : libellé de disponibilité, ou null si le restaurant n'est pas commandable. */
export function availability(store, whenMs, nowMs) {
  if (whenMs == null || whenMs - nowMs <= NOW_MARGIN_MIN * 60000) {
    if (!store.is_open) return null;
    if (store.hours != null && !isOpenAt(store.hours, parisParts(nowMs))) return null;
    return store.open_until || "Ouvert";
  }
  const when = parisParts(whenMs);
  const slots = store.delivery_slots || {};
  const horizon = Object.keys(slots).sort();
  if (horizon.length && horizon[0] <= when.date && when.date <= horizon[horizon.length - 1]) {
    // Les créneaux se chevauchent : on garde celui qui commence le plus près de l'heure visée.
    const matching = (slots[when.date] || []).filter(([s, e]) => s <= when.minute && when.minute < e);
    if (!matching.length) return null;
    const [start, end] = matching.reduce((a, b) => (b[0] > a[0] || (b[0] === a[0] && b[1] > a[1]) ? b : a));
    return `Créneau ${hhmm(start)}–${hhmm(end)}`;
  }
  if (store.hours != null && isOpenAt(store.hours, when)) {
    return "Ouvert à cette heure (non planifiable : commander au moment voulu)";
  }
  return null;
}

// --- Paniers, frais et promos (selection.py) --------------------------------------------------

export function itemCost(it, qty) {
  let free = 0;
  if (it.bogo_get && it.bogo_buy) {
    let groups = Math.floor(qty / (it.bogo_buy + it.bogo_get));
    if (it.bogo_max) groups = Math.min(groups, it.bogo_max);
    free = groups * it.bogo_get;
  }
  return [pyRound(it.price * (qty - free), 2), free];
}

function uberOneApplies(subtotal, fees) {
  const one = fees.uber_one || {};
  return Boolean(one.enabled) && subtotal >= one.min_subtotal;
}

export function computeFees(subtotal, store, fees) {
  let delivery = store.delivery_fee != null ? store.delivery_fee : fees.default_delivery_fee;
  if (store.free_delivery_min != null && subtotal >= store.free_delivery_min) delivery = 0.0;
  let service = Math.min(Math.max(subtotal * fees.service_fee_pct, fees.service_fee_min), fees.service_fee_max);
  if (uberOneApplies(subtotal, fees)) {
    delivery = 0.0;
    service *= 1 - fees.uber_one.service_fee_discount;
  }
  const small = subtotal < fees.small_order_threshold ? fees.small_order_fee : 0.0;
  return [pyRound(delivery, 2), pyRound(service, 2), pyRound(small, 2)];
}

function bestThresholdDiscount(subtotal, store) {
  let best = 0.0, bestLabel = null;
  const gifts = [];
  for (const p of store.threshold_promos) {
    if (subtotal < p.min_subtotal) continue;
    const value = pyRound(subtotal * p.pct_off + p.amount_off, 2);
    if (value > best) [best, bestLabel] = [value, p.label];
    else if (value === 0) gifts.push(p.label);
  }
  return [best, (bestLabel ? [bestLabel] : []).concat(gifts)];
}

function buildBasket(store, people, lines, fees) {
  let subtotal = 0.0;
  const promos = [];
  for (const [it, qty] of lines) {
    const [cost, free] = itemCost(it, qty);
    subtotal += cost;
    if (free) promos.push(`${it.bogo_buy} acheté = ${it.bogo_get} offert : ${it.title}`);
    if (it.original_price && it.original_price > it.price) {
      promos.push(`-${pyRound(100 * (1 - it.price / it.original_price))} % sur ${it.title}`);
    }
  }
  subtotal = pyRound(subtotal, 2);
  const [discount, thresholdLabels] = bestThresholdDiscount(subtotal, store);
  const [delivery, service, small] = computeFees(subtotal, store, fees);
  if (uberOneApplies(subtotal, fees)) promos.push("Uber One : livraison offerte, frais de service réduits");
  else if (delivery === 0) promos.push("Livraison offerte");
  const total = pyRound(subtotal - discount + delivery + service + small, 2);
  return {
    store, people, lines, items_subtotal: subtotal, threshold_discount: discount,
    delivery_fee: delivery, service_fee: service, small_order_fee: small,
    promos_used: promos.concat(thresholdLabels), total, per_person: pyRound(total / people, 2),
    score: 0, availability: "",
  };
}

function unitLowerBound(it) {
  return it.bogo_get && it.bogo_buy ? it.price * it.bogo_buy / (it.bogo_buy + it.bogo_get) : it.price;
}

/** Bornes et coûts en centimes des plats candidats, communs à tous les seuils d'un panier. */
function centsTables(items, n) {
  const m = items.length;
  const lb = items.map((it) => pyRound(unitLowerBound(it) * 100));
  const suffixMax = new Array(m + 1).fill(0);
  for (let i = m - 1; i >= 0; i--) suffixMax[i] = Math.max(pyRound(items[i].price * 100), suffixMax[i + 1]);
  const cost = items.map((it) => Array.from({ length: n + 1 }, (_, q) => pyRound(itemCost(it, q)[0] * 100)));
  return { lb, suffixMax, cost };
}

/** Combinaison de `n` unités au sous-total minimal (en centimes), sous-total >= floorCents. */
function minSubtotalLines(items, n, floorCents, minDistinct, { lb, suffixMax, cost }) {
  const m = items.length;
  let bestCost = Infinity, bestChosen = null;
  const chosen = [];

  function dfs(i, rem, partial, distinct) {
    if (rem === 0) {
      if (partial >= floorCents && distinct >= minDistinct && partial < bestCost) {
        bestCost = partial;
        bestChosen = chosen.slice();
      }
      return;
    }
    if (i === m || distinct + Math.min(rem, m - i) < minDistinct) return;
    if (partial + rem * suffixMax[i] < floorCents) return;
    if (Math.max(partial + rem * lb[i], floorCents) >= bestCost) return;
    for (let q = rem; q >= 0; q--) {
      if (q) chosen.push([i, q]);
      dfs(i + 1, rem - q, partial + cost[i][q], distinct + (q > 0 ? 1 : 0));
      if (q) chosen.pop();
    }
  }

  dfs(0, n, 0, 0);
  return bestChosen === null ? null : bestChosen.map(([i, q]) => [items[i], q]);
}

function bestBasket(store, people, fees, minDistinct, itemFilter) {
  // Tri stable une fois pour toutes : filtrer la liste triée revient à trier la liste filtrée.
  store._byBound ??= store.items.slice().sort((a, b) => unitLowerBound(a) - unitLowerBound(b));
  const mains = itemFilter ? store._byBound.filter(itemFilter) : store._byBound;
  minDistinct = Math.min(minDistinct, people);
  if (mains.length < minDistinct || !mains.length) return null;
  const candidates = mains.slice(0, MAX_CANDIDATES).concat(mains.slice(MAX_CANDIDATES).filter((it) => it.bogo_get));

  const floors = new Set([0, fees.small_order_threshold, ...store.threshold_promos.map((p) => p.min_subtotal)]);
  if (store.free_delivery_min != null) floors.add(store.free_delivery_min);
  if ((fees.uber_one || {}).enabled) floors.add(fees.uber_one.min_subtotal);
  const tables = centsTables(candidates, people);
  let best = null;
  for (const floor of [...floors].sort((a, b) => a - b)) {
    const lines = minSubtotalLines(candidates, people, pyRound(floor * 100), minDistinct, tables);
    if (lines === null) continue;
    const b = buildBasket(store, people, lines, fees);
    if (best === null || b.total < best.total) best = b;
  }
  return best;
}

function scoreBasket(b, cfg) {
  const s = cfg.scoring;
  const price = 1 - b.per_person / cfg.selection.max_price_per_person;
  const rating = (b.store.rating || 4.0) / 5;
  const maxEta = cfg.prefilter.max_eta_min;
  const etaMid = ((b.store.eta_min || maxEta) + (b.store.eta_max || maxEta)) / 2;
  const eta = Math.max(0.0, 1 - etaMid / maxEta);
  return pyRound(s.w_price * price + s.w_rating * rating + s.w_eta * eta, 4);
}

export function brandKey(title) {
  const t = title.split(" - ")[0].normalize("NFKD").replace(/[^\x00-\x7f]/g, "");
  return t.toLowerCase().replace(/[^a-z0-9]+/g, " ").split(" ").filter(Boolean).join(" ");
}

function compositionKey(b) {
  const lines = b.lines.map(([it, q]) => [it.title.toLowerCase(), q])
    .sort((x, y) => (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : x[1] - y[1]));
  return JSON.stringify([lines, b.items_subtotal]);
}

/**
 * Top N des paniers pour `people` personnes (select_deals pour une seule taille de groupe).
 * `cfg` : data.config, éventuellement modifié (Uber One, budget). `whenMs` : heure de livraison
 * visée ; `nowMs` : instant de référence de l'ouverture (l'heure de la récupération).
 */
export function selectDeals(stores, cfg, opts) {
  return rankDeals(bestBaskets(stores, cfg, opts), cfg, opts.topN ?? 10);
}

/** Meilleur panier de chaque restaurant disponible, sans tenir compte du budget. */
export function bestBaskets(stores, cfg, { people, whenMs = null, nowMs, mealTypes = null }) {
  const available = new Map();
  for (const s of stores) {
    const label = availability(s, whenMs, nowMs);
    if (label !== null) available.set(s.uuid, label);
  }
  const wanted = mealTypes && mealTypes.length ? new Set(mealTypes) : null;
  const itemFilter = wanted ? (it) => it.types.some((t) => wanted.has(t)) : null;
  const baskets = [];
  for (const store of stores) {
    if (!available.has(store.uuid)) continue;
    const b = bestBasket(store, people, cfg.fees, cfg.selection.min_distinct_items ?? 1, itemFilter);
    if (b) {
      b.availability = available.get(store.uuid);
      baskets.push(b);
    }
  }
  return baskets;
}

/** Paniers sous le budget, classés par score, une seule fois par enseigne. Rapide : le curseur
 *  de budget ne recalcule que cette étape. */
export function rankDeals(all, cfg, topN = 10) {
  const baskets = all.filter((b) => b.per_person < cfg.selection.max_price_per_person);
  for (const b of baskets) b.score = scoreBasket(b, cfg);
  baskets.sort((a, b) => b.score - a.score);
  // Une même enseigne (ou un même panier sous deux noms) n'apparaît qu'une fois.
  const kept = [], seen = new Set();
  for (const b of baskets) {
    const keys = [`brand:${brandKey(b.store.title)}`, `compo:${compositionKey(b)}`];
    if (keys.some((k) => seen.has(k))) continue;
    keys.forEach((k) => seen.add(k));
    kept.push(b);
  }
  return kept.slice(0, topN);
}
