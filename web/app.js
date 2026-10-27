// Interface de DailyUber : charge data.json (recherche quotidienne) et recalcule les offres
// dans le téléphone à chaque réglage, sans aucune requête vers Uber Eats.
import { availability, bestBaskets, hhmm, parisParts, parisToMs, rankDeals } from "./engine.js";

const PREFS_KEY = "dailyuber:prefs";
const FAVS_KEY = "dailyuber:favorites"; // [{uuid, title, url}] : titre et lien gardés si le restaurant disparaît
const TABS = [["deals", "Offres"], ["favs", "Favoris"]];
const PLATFORMS = { uber: "Uber Eats", deliveroo: "Deliveroo" };
const platformOf = (s) => s.platform || (String(s.uuid).startsWith("deliveroo:") ? "deliveroo" : "uber");
const platformName = (s) => PLATFORMS[platformOf(s)] || platformOf(s);
const PEOPLE = [1, 2, 3, 4, 5, 6];
const WHEN = [["now", "Maintenant"], ["today", "Aujourd'hui"], ["tomorrow", "Demain"]];
const TOP_N = 10;
const DAY_MS = 86400000;

const $ = (id) => document.getElementById(id);
const euro = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" });
const euroShort = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", minimumFractionDigits: 0, maximumFractionDigits: 2 });
const count = new Intl.NumberFormat("fr-FR");

const ICONS = {
  fav: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.2l2.7 5.6 6.1.8-4.5 4.3 1.1 6.1L12 17.1 6.6 20l1.1-6.1-4.5-4.3 6.1-.8z"/></svg>',
  star: '<svg class="star" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6L12 17.3l-5.9 3.3 1.3-6.6-4.9-4.6 6.6-.8z"/></svg>',
  chevron: '<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>',
  arrow: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17L17 7M9 7h8v8"/></svg>',
  bag: '<svg viewBox="0 0 48 48" aria-hidden="true"><path d="M10 16h28l-2 24H12z"/><path d="M18 16v-3a6 6 0 0 1 12 0v3"/></svg>',
  cloud: '<svg viewBox="0 0 48 48" aria-hidden="true"><path d="M14 36h20a8 8 0 0 0 1-15.9A11 11 0 0 0 14 22a7 7 0 0 0 0 14z"/><path d="M18 30l12-8"/></svg>',
};

let data = null;
const prefs = loadPrefs();
let favs = loadFavs();
let index = new Map(); // uuid -> restaurant avec menu (data.stores) ou sans (data.catalog)
// Position GPS pour "Autour de moi" : jamais enregistrée, redemandée à chaque ouverture.
const geo = { pos: null, status: "off" }; // status : off | pending | ok | denied | unavailable
let memo = { key: "", baskets: [] }; // paniers du dernier calcul : le budget ne fait que les filtrer

// --- Préférences (mémorisées sur le téléphone) ---------------------------------------------

function loadPrefs() {
  const defaults = { tab: "deals", people: 2, when: "now", time: defaultTime(), budget: 10, uberOne: false, types: [],
    nearMe: false, radiusKm: 2, platform: "all" };
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") };
  } catch {
    return defaults;
  }
}

function savePrefs() {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* navigation privée */ }
}

// --- Favoris (mémorisés sur le téléphone ; ceux de config.yaml viennent de data.json) ---------

function loadFavs() {
  try {
    const list = JSON.parse(localStorage.getItem(FAVS_KEY) || "[]");
    return Array.isArray(list) ? list.filter((f) => f && f.uuid) : [];
  } catch {
    return [];
  }
}

function saveFavs() {
  try { localStorage.setItem(FAVS_KEY, JSON.stringify(favs)); } catch { /* navigation privée */ }
}

const configFavs = () => new Set(data.favorites || []);
const isFav = (uuid) => configFavs().has(uuid) || favs.some((f) => f.uuid === uuid);

/** Favoris dans l'ordre : ceux de config.yaml, puis ceux ajoutés sur le téléphone. */
function favoriteUuids() {
  return [...new Set([...(data.favorites || []), ...favs.map((f) => f.uuid)])];
}

function toggleFav(uuid) {
  if (configFavs().has(uuid)) return; // défini dans config.yaml : on ne le retire pas d'ici
  if (favs.some((f) => f.uuid === uuid)) favs = favs.filter((f) => f.uuid !== uuid);
  else {
    const s = index.get(uuid);
    favs.push({ uuid, title: s ? s.title : "", url: s ? s.url : "" });
  }
  saveFavs();
  changed();
}

// --- Autour de moi ---------------------------------------------------------------------------

/** Distance à vol d'oiseau en km (formule de haversine). */
function distanceKm(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

/** Distance du restaurant à ma position, ou null (position inconnue, coordonnées absentes). */
function storeDistance(s) {
  return geo.pos && s.lat != null && s.lng != null ? distanceKm(geo.pos, s) : null;
}

const km = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 });
const kmLabel = (d) => (d < 1 ? `${Math.max(50, Math.round(d * 1000 / 50) * 50)} m` : `${km.format(d)} km`);

function locate() {
  if (!("geolocation" in navigator)) {
    geo.status = "unavailable";
    changed();
    return;
  }
  geo.status = "pending";
  syncControls();
  navigator.geolocation.getCurrentPosition(
    (p) => {
      geo.pos = { lat: p.coords.latitude, lng: p.coords.longitude };
      geo.status = "ok";
      changed();
    },
    (err) => {
      geo.status = err.code === err.PERMISSION_DENIED ? "denied" : "unavailable";
      changed();
    },
    { enableHighAccuracy: false, timeout: 15000, maximumAge: 5 * 60000 },
  );
}

/** Le filtre "Autour de moi" s'applique-t-il ? (sinon, tous les restaurants) */
const nearActive = () => prefs.nearMe && geo.status === "ok";

function nearCaption() {
  if (!prefs.nearMe) return "Seulement les restaurants proches de ta position";
  if (geo.status === "pending") return "Recherche de ta position…";
  if (geo.status === "denied") return "Localisation refusée : autorise-la dans les réglages du navigateur";
  if (geo.status === "unavailable") return "Position introuvable pour le moment : tous les restaurants sont affichés";
  if (geo.status === "ok" && data.center) return `Tu es à ${kmLabel(distanceKm(geo.pos, data.center))} du ${data.address.split(",")[0]}`;
  return "Position trouvée";
}

/** Heure proposée par défaut : midi et demi le matin, 19h30 l'après-midi. */
function defaultTime() {
  const m = parisParts(Date.now()).minute;
  if (m < 12 * 60) return "12:30";
  if (m < 19 * 60) return "19:30";
  return hhmm(Math.ceil((m + 30) / 30) * 30);
}

// --- Contrôles -------------------------------------------------------------------------------

function segmented(el, options, current, onChange) {
  el.style.setProperty("--n", options.length);
  el.innerHTML = options.map(([value, label]) =>
    `<button type="button" role="radio" data-value="${value}">${label}</button>`).join("");
  const update = (value) => {
    const i = options.findIndex(([v]) => String(v) === String(value));
    el.style.setProperty("--i", Math.max(i, 0));
    for (const b of el.children) b.setAttribute("aria-checked", String(b.dataset.value === String(value)));
  };
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    update(b.dataset.value);
    onChange(b.dataset.value);
  });
  update(current);
  requestAnimationFrame(() => el.classList.add("ready")); // pas de glissement à l'ouverture
}

function setupControls() {
  segmented($("tab"), TABS, prefs.tab, (v) => { prefs.tab = v; changed(); });
  segmented($("people"), PEOPLE.map((n) => [n, n]), prefs.people, (v) => { prefs.people = Number(v); changed(); });
  segmented($("when"), WHEN, prefs.when, (v) => { prefs.when = v; changed(); });
  // Réglage affiché seulement si la recherche du jour couvre plusieurs plateformes.
  const platforms = [...new Set(data.stores.map((s) => s.platform || "uber"))];
  $("platform-row").hidden = platforms.length < 2;
  if (!platforms.includes(prefs.platform)) prefs.platform = "all";
  segmented($("platform"), [["all", "Toutes"], ...platforms.map((p) => [p, PLATFORMS[p] || p])], prefs.platform,
    (v) => { prefs.platform = v; changed(); });

  $("time").value = prefs.time;
  $("time").addEventListener("change", (e) => { prefs.time = e.target.value || prefs.time; changed(); });

  const budget = $("budget");
  budget.value = prefs.budget;
  budget.addEventListener("input", () => { prefs.budget = Number(budget.value); changed(); });

  $("near-me").checked = prefs.nearMe;
  $("near-me").addEventListener("change", (e) => {
    prefs.nearMe = e.target.checked;
    if (prefs.nearMe) locate(); // position fraîche à chaque activation
    changed();
  });
  const radius = $("radius");
  radius.value = prefs.radiusKm;
  radius.addEventListener("input", () => { prefs.radiusKm = Number(radius.value); changed(); });
  if (prefs.nearMe) locate();

  $("uber-one").checked = prefs.uberOne;
  $("uber-one").addEventListener("change", (e) => { prefs.uberOne = e.target.checked; changed(); });

  const types = [["", "Tout"], ...data.meal_types.map((t) => [t.id, t.label])];
  prefs.types = prefs.types.filter((t) => data.meal_types.some((m) => m.id === t));
  $("types").innerHTML = types.map(([id, label]) =>
    `<button type="button" class="chip" data-type="${id}">${label}</button>`).join("");
  $("types").addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (!b) return;
    const t = b.dataset.type;
    if (!t) prefs.types = [];
    else prefs.types = prefs.types.includes(t) ? prefs.types.filter((x) => x !== t) : [...prefs.types, t];
    changed();
  });

  $("results").addEventListener("click", (e) => {
    const b = e.target.closest(".fav-btn");
    if (b) toggleFav(b.dataset.uuid);
  });
  $("fav-search").addEventListener("input", renderMatches);
  $("fav-matches").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-uuid]");
    if (!b) return;
    toggleFav(b.dataset.uuid);
    $("fav-search").value = "";
    renderMatches();
  });
}

function syncControls() {
  const onFavs = prefs.tab === "favs";
  $("types-section").hidden = onFavs; // l'onglet Favoris montre le meilleur panier, tous plats confondus
  $("fav-add").hidden = !onFavs;
  const n = favoriteUuids().length;
  $("tab").querySelector('[data-value="favs"]').textContent = n ? `Favoris (${n})` : "Favoris";
  $("time-row").hidden = prefs.when === "now";
  $("budget-value").textContent = `${euroShort.format(prefs.budget)} / pers`;
  const b = $("budget");
  b.style.setProperty("--pct", `${((prefs.budget - b.min) / (b.max - b.min)) * 100}%`);
  $("radius-row").hidden = !prefs.nearMe;
  $("near-caption").textContent = nearCaption();
  $("radius-value").textContent = `${km.format(prefs.radiusKm)} km`;
  const r = $("radius");
  r.style.setProperty("--pct", `${((prefs.radiusKm - r.min) / (r.max - r.min)) * 100}%`);
  for (const chip of $("types").children) {
    const t = chip.dataset.type;
    chip.setAttribute("aria-pressed", String(t ? prefs.types.includes(t) : prefs.types.length === 0));
  }
}

let pending = 0;
function changed() {
  savePrefs();
  syncControls();
  clearTimeout(pending);
  pending = setTimeout(render);
}

// --- Calcul et affichage ---------------------------------------------------------------------

/** Heure de livraison visée (ms) et message éventuel. */
function target() {
  const now = Date.now();
  if (prefs.when === "now") return { whenMs: now, hint: "" };
  const [h, m] = prefs.time.split(":").map(Number);
  const day = parisParts(now + (prefs.when === "tomorrow" ? DAY_MS : 0)).date;
  const whenMs = parisToMs(day, h * 60 + m);
  if (whenMs < now) return { whenMs: now, hint: "Cette heure est déjà passée : offres pour maintenant." };
  return { whenMs, hint: "" };
}

function render() {
  const { whenMs, hint } = target();
  $("time-hint").hidden = !hint;
  $("time-hint").textContent = hint;

  const cfg = structuredClone(data.config);
  cfg.fees.uber_one.enabled = prefs.uberOne;
  cfg.selection.max_price_per_person = prefs.budget;
  if (prefs.tab === "favs") {
    renderFavorites(cfg, whenMs);
    return;
  }
  $("results-caption").textContent = "Les mieux notés d'abord, puis les moins chers";
  const key = [prefs.people, prefs.uberOne, prefs.types.join(","), Math.floor(whenMs / 60000), data.fetched_at].join("|");
  if (key !== memo.key) {
    memo = { key, baskets: bestBaskets(data.stores, cfg, {
      people: prefs.people, whenMs, nowMs: Date.parse(data.fetched_at), mealTypes: prefs.types }) };
  }
  // Le rayon et la plateforme ne font que filtrer les paniers déjà calculés, comme le budget.
  const pool = memo.baskets.filter((b) => {
    if (prefs.platform !== "all" && (b.store.platform || "uber") !== prefs.platform) return false;
    if (!nearActive()) return true;
    const d = storeDistance(b.store);
    return d !== null && d <= prefs.radiusKm;
  });
  const deals = rankDeals(pool, cfg, TOP_N, "rating");

  const n = deals.length;
  $("results-title").textContent = n ? `${n} offre${n > 1 ? "s" : ""} pour ${prefs.people}` : "Aucune offre";
  $("results").innerHTML = n ? deals.map(dealCard).join("") : emptyState();
  const raise = $("raise-budget");
  if (raise) raise.addEventListener("click", () => { prefs.budget = Number($("budget").max); $("budget").value = prefs.budget; changed(); });
  const all = $("all-types");
  if (all) all.addEventListener("click", () => { prefs.types = []; changed(); });
  const widen = $("widen-radius");
  if (widen) widen.addEventListener("click", () => { prefs.radiusKm = Number($("radius").max); $("radius").value = prefs.radiusKm; changed(); });
}

/** Onglet Favoris : meilleur panier de chaque favori avec les réglages du moment (personnes,
 *  heure, Uber One), tous types de plats, même au-dessus du budget. */
function renderFavorites(cfg, whenMs) {
  const nowMs = Date.parse(data.fetched_at);
  const deals = [], others = [];
  for (const uuid of favoriteUuids()) {
    const s = index.get(uuid);
    if (!s) {
      const f = favs.find((x) => x.uuid === uuid) || { uuid, title: "Restaurant", url: "" };
      others.push(infoCard(f, "Absent de la recherche du jour : il ne livre peut-être pas à cette adresse aujourd'hui."));
    } else if (!s.items) {
      others.push(infoCard(s, configFavs().has(uuid)
        ? "Menu pas récupéré aujourd'hui."
        : "Menu pas analysé aujourd'hui : ajoute ce restaurant à « favorites » dans config.yaml pour qu'il le soit chaque jour."));
    } else if (availability(s, whenMs, nowMs) === null) {
      others.push(infoCard(s, prefs.when === "now" ? "Fermé en ce moment." : "Pas de livraison possible à cette heure."));
    } else {
      const b = bestBaskets([s], cfg, { people: prefs.people, whenMs, nowMs, mealTypes: null })[0];
      if (b) deals.push(b);
      else others.push(infoCard(s, "Aucun plat principal reconnu dans son menu."));
    }
  }
  deals.sort((a, b) => a.per_person - b.per_person);
  const n = deals.length + others.length;
  $("results-title").textContent = n ? `${n} favori${n > 1 ? "s" : ""}` : "Favoris";
  $("results-caption").textContent = "Meilleur panier avec tes réglages, même au-dessus du budget";
  $("results").innerHTML = n
    ? deals.map((b, k) => dealCard(b, k, { favorites: true })).join("") + others.join("")
    : `<li class="empty">${ICONS.fav}<h3>Aucun favori</h3>
       <p>Touche l'étoile d'une offre, ou cherche un restaurant ci-dessous.</p></li>`;
}

/** Liste des restaurants qui correspondent à la recherche d'un favori. */
function renderMatches() {
  const norm = (t) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const q = norm($("fav-search").value.trim());
  if (!q) {
    $("fav-matches").innerHTML = "";
    return;
  }
  const found = [...index.values()].filter((s) => !isFav(s.uuid) && norm(s.title).includes(q))
    .sort((a, b) => (b.rating || 0) - (a.rating || 0)).slice(0, 8);
  $("fav-matches").innerHTML = found.length
    ? found.map((s) => `
      <li><span class="match-name">${esc(s.title)}${s.rating ? `<span class="match-meta">${ICONS.star}${s.rating.toFixed(1).replace(".", ",")}</span>` : ""}</span>
          <button type="button" data-uuid="${esc(s.uuid)}">Ajouter</button></li>`).join("")
    : "<li class=\"match-none\">Aucun restaurant de ce nom aujourd'hui.</li>";
}

function favButton(uuid) {
  const fromConfig = configFavs().has(uuid), on = isFav(uuid);
  const label = fromConfig ? "Favori de config.yaml" : on ? "Retirer des favoris" : "Ajouter aux favoris";
  return `<button type="button" class="fav-btn" data-uuid="${esc(uuid)}" aria-pressed="${on}"
    aria-label="${label}" title="${label}"${fromConfig ? " disabled" : ""}>${ICONS.fav}</button>`;
}

function storeMeta(s) {
  const meta = [`<span class="platform">${esc(platformName(s))}</span>`];
  if (s.rating) meta.push(`${ICONS.star}${s.rating.toFixed(1).replace(".", ",")}${s.rating_count ? ` (${count.format(s.rating_count)})` : ""}`);
  if (s.eta_min) meta.push(s.eta_min === s.eta_max || !s.eta_max ? `${s.eta_min} min` : `${s.eta_min}–${s.eta_max} min`);
  const d = storeDistance(s);
  if (d !== null) meta.push(kmLabel(d));
  return meta;
}

/** Favori sans panier à afficher (fermé, menu non analysé, absent du jour). */
function infoCard(s, reason) {
  const meta = storeMeta(s);
  return `
  <li class="deal deal-muted">
    <div class="deal-head">
      <div>
        <h3 class="deal-name">${esc(s.title)}</h3>
        ${meta.length ? `<p class="deal-meta">${meta.join(" · ")}</p>` : ""}
        ${configFavs().has(s.uuid) ? '<p class="deal-tag">Suivi chaque jour</p>' : ""}
      </div>
      ${favButton(s.uuid)}
    </div>
    <p class="deal-reason">${esc(reason)}</p>
    ${s.url ? `<a class="order secondary" href="${esc(s.url)}" target="_blank" rel="noopener">Voir sur ${esc(platformName(s))} ${ICONS.arrow}</a>` : ""}
  </li>`;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/** Libellés courts pour les pastilles de promo. */
function pills(promos) {
  const out = promos.map((p) => {
    if (p.startsWith("Uber One")) return "Uber One";
    if (/ offert : /.test(p)) return p.split(" : ")[0];
    const pct = p.match(/^-(\d+) % sur /);
    if (pct) return `-${pct[1]} % sur un plat`;
    return p;
  });
  return [...new Set(out)];
}

function availabilityLabel(text) {
  if (text.startsWith("Ouvert à cette heure")) return "Ouvert à cette heure, sans commande à l'avance";
  return text;
}

function dealCard(b, k, { favorites = false } = {}) {
  const s = b.store;
  const meta = storeMeta(s);
  const over = favorites && b.per_person >= prefs.budget;

  const lines = b.lines.map(([it, q]) => `
    <li><span><span class="qty">${q} ×</span>${esc(it.title)}</span>
        <span class="price">${it.has_customizations ? "dès " : ""}${euro.format(it.price)}</span></li>`).join("");
  const promo = pills(b.promos_used);
  const rows = [["Articles", b.items_subtotal]];
  if (b.threshold_discount) rows.push(["Remise", -b.threshold_discount]);
  rows.push(["Livraison", b.delivery_fee], ["Service (estimé)", b.service_fee]);
  if (b.small_order_fee) rows.push(["Petite commande", b.small_order_fee]);

  return `
  <li class="deal" style="--k:${k}">
    <div class="deal-head">
      <div>
        <h3 class="deal-name">${favorites ? "" : `<span class="deal-rank">${k + 1}</span>`}${esc(s.title)}</h3>
        ${meta.length ? `<p class="deal-meta">${meta.join(" · ")}</p>` : ""}
        ${b.availability ? `<p class="deal-when">${esc(availabilityLabel(b.availability))}</p>` : ""}
        ${favorites && configFavs().has(s.uuid) ? '<p class="deal-tag">Suivi chaque jour</p>' : ""}
      </div>
      <div class="deal-side">
        ${favButton(s.uuid)}
        <div class="deal-price${over ? " over" : ""}"><strong>${euro.format(b.per_person)}</strong>
          <span>${over ? `au-dessus de ${euroShort.format(prefs.budget)}` : "par personne"}</span></div>
      </div>
    </div>
    <ul class="lines">${lines}</ul>
    ${promo.length ? `<ul class="pills">${promo.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
    <details class="details">
      <summary><span>Total <strong>${euro.format(b.total)}</strong></span><span class="more">Détail ${ICONS.chevron}</span></summary>
      <dl class="breakdown">${rows.map(([l, v]) => `<dt>${l}</dt><dd>${v < 0 ? "−" : ""}${euro.format(Math.abs(v))}</dd>`).join("")}</dl>
    </details>
    <a class="order" href="${esc(s.url)}" target="_blank" rel="noopener">Commander sur ${esc(platformName(s))} ${ICONS.arrow}</a>
  </li>`;
}

function emptyState() {
  const budgetMax = Number($("budget").max), radiusMax = Number($("radius").max);
  const actions = [];
  if (nearActive() && prefs.radiusKm < radiusMax) actions.push(`<button type="button" id="widen-radius">Élargir à ${km.format(radiusMax)} km</button>`);
  else if (prefs.types.length) actions.push('<button type="button" id="all-types">Tous les types de repas</button>');
  else if (prefs.budget < budgetMax) actions.push(`<button type="button" id="raise-budget">Monter à ${euroShort.format(budgetMax)} / pers</button>`);
  return `
  <li class="empty">
    ${ICONS.bag}
    <h3>Rien sous ${euroShort.format(prefs.budget)} par personne${nearActive() ? ` à moins de ${km.format(prefs.radiusKm)} km` : ""}</h3>
    <p>Essaie un autre type de repas, une autre heure, un budget plus élevé${nearActive() ? " ou un rayon plus large" : ""}.</p>
    ${actions.join("")}
  </li>`;
}

function errorState(retry) {
  $("results-title").textContent = "Offres";
  $("results").innerHTML = `
  <li class="empty">
    ${ICONS.cloud}
    <h3>Impossible de charger les offres</h3>
    <p>Vérifie ta connexion internet, puis réessaie.</p>
    <button type="button" id="retry">Réessayer</button>
  </li>`;
  $("retry").addEventListener("click", retry);
}

// --- En-tête : fraîcheur des données ----------------------------------------------------------

function describeFetch() {
  const fetched = Date.parse(data.fetched_at);
  const f = parisParts(fetched), today = parisParts(Date.now()).date;
  const time = `${Math.floor(f.minute / 60)} h ${String(f.minute % 60).padStart(2, "0")}`;
  const yesterday = parisParts(Date.now() - DAY_MS).date;
  const label = f.date === today ? `aujourd'hui à ${time}`
    : f.date === yesterday ? `hier à ${time}`
    : new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", weekday: "long", day: "numeric", month: "long" }).format(fetched);
  const place = data.address.split(",")[0];
  $("subtitle").textContent = `${place} · prix relevés ${label}`;
  const banner = $("banner");
  banner.hidden = f.date === today;
  if (!banner.hidden) banner.textContent = "La recherche du jour n'a pas encore eu lieu : les prix et les promos ont pu changer depuis.";
}

// --- Démarrage -------------------------------------------------------------------------------

function watchTitle() {
  const nav = $("nav");
  new IntersectionObserver(([e]) => nav.classList.toggle("visible", !e.isIntersecting), { rootMargin: "-44px 0px 0px 0px" })
    .observe($("title"));
}

async function load() {
  $("results").innerHTML = '<li class="skeleton"></li><li class="skeleton"></li><li class="skeleton"></li>';
  try {
    const res = await fetch("data.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(res.status);
    data = await res.json();
  } catch {
    errorState(load);
    $("subtitle").textContent = "Hors connexion";
    return;
  }
  index = new Map();
  for (const s of data.catalog || []) index.set(s.uuid, s);
  for (const s of data.stores) index.set(s.uuid, s); // avec menu : remplace la version légère
  describeFetch();
  if (!$("types").children.length) setupControls();
  syncControls();
  render();
}

watchTitle();
load();
// Les créneaux "maintenant" dépendent de l'heure : on recalcule quand l'appli revient au premier plan.
document.addEventListener("visibilitychange", () => {
  if (document.hidden || !data) return;
  describeFetch();
  if (prefs.nearMe) locate(); // on a pu se déplacer depuis la dernière ouverture
  render();
});
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
