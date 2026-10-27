"""Composition des paniers, calcul des frais et des promos, filtrage et classement."""
import re
import unicodedata
from datetime import datetime

from .hours import PARIS, availability
from .models import Basket, Item, Store

MAX_CANDIDATES = 40  # plats les moins chers considérés par restaurant (plus tous les 1+1)


def _kw_regex(keywords: list[str]) -> re.Pattern:
    # Ancré en début de mot : "eau" ne matche pas "plateau", "frite" matche "frites".
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in keywords) + ")", re.I)


class MealClassifier:
    def __init__(self, meal_cfg: dict):
        self.min_price = meal_cfg["min_item_price"]
        self.main = _kw_regex(meal_cfg["main_keywords"])
        self.exclude = _kw_regex(meal_cfg["exclude_keywords"])
        always = meal_cfg.get("always_exclude_keywords") or []
        self.always_exclude = _kw_regex(always) if always else None

    def is_main(self, item: Item) -> bool:
        if item.price < self.min_price:
            return False
        if self.always_exclude and self.always_exclude.search(item.title):
            return False
        title_main = bool(self.main.search(item.title))
        section_main = bool(self.main.search(item.section))
        # "Frites" est exclu, mais "Menu burger frites" reste un plat.
        if self.exclude.search(item.title) and not title_main:
            return False
        # Une section "Accompagnements" exclut même "Riz crevettes", sauf si elle s'appelle aussi "Menus".
        if self.exclude.search(item.section) and not section_main:
            return False
        return title_main or section_main


def _norm(text: str) -> str:
    return " ".join(text.replace(" ", " ").lower().split())


class MealTypes:
    """Types de repas d'un plat : mots-clés du titre ou de la section, sinon ceux du restaurant
    (mots-clés dans son nom, ou sa liste de cuisines)."""

    def __init__(self, types_cfg: dict):
        self.keywords = {t: _kw_regex(c["keywords"]) for t, c in types_cfg.items()}
        self.cuisines = {t: {_norm(x) for x in c.get("cuisines", [])} for t, c in types_cfg.items()}

    def of(self, item: Item, store: Store) -> list[str]:
        text = f"{item.title} {item.section}"
        found = [t for t, rx in self.keywords.items() if rx.search(text)]
        if found:
            return found
        cuisines = {_norm(c) for c in store.cuisines}
        return [t for t, cs in self.cuisines.items() if cs & cuisines or self.keywords[t].search(store.title)]


def item_cost(item: Item, qty: int) -> tuple[float, int]:
    """Coût de `qty` unités d'un article, et nombre d'unités offertes ("X achetés = Y offerts")."""
    free = 0
    if item.bogo_get and item.bogo_buy:
        groups = qty // (item.bogo_buy + item.bogo_get)
        if item.bogo_max:
            groups = min(groups, item.bogo_max)
        free = groups * item.bogo_get
    return round(item.price * (qty - free), 2), free


def uber_one_applies(items_subtotal: float, fees_cfg: dict) -> bool:
    one = fees_cfg.get("uber_one") or {}
    return bool(one.get("enabled")) and items_subtotal >= one["min_subtotal"]


def compute_fees(items_subtotal: float, store: Store, fees_cfg: dict) -> tuple[float, float, float]:
    """(frais de livraison, frais de service, frais de petite commande)."""
    delivery = store.delivery_fee if store.delivery_fee is not None else fees_cfg["default_delivery_fee"]
    if store.free_delivery_min is not None and items_subtotal >= store.free_delivery_min:
        delivery = 0.0
    service = min(max(items_subtotal * fees_cfg["service_fee_pct"], fees_cfg["service_fee_min"]),
                  fees_cfg["service_fee_max"])
    if uber_one_applies(items_subtotal, fees_cfg):
        delivery = 0.0
        service *= 1 - fees_cfg["uber_one"]["service_fee_discount"]
    small = fees_cfg["small_order_fee"] if items_subtotal < fees_cfg["small_order_threshold"] else 0.0
    return round(delivery, 2), round(service, 2), round(small, 2)


def best_threshold_discount(items_subtotal: float, store: Store) -> tuple[float, list[str]]:
    """Meilleure promo "dès Y €" applicable (non cumulables) + promos cadeau signalées."""
    best, best_label, gifts = 0.0, None, []
    for p in store.threshold_promos:
        if items_subtotal < p.min_subtotal:
            continue
        value = round(items_subtotal * p.pct_off + p.amount_off, 2)
        if value > best:
            best, best_label = value, p.label
        elif value == 0:
            gifts.append(p.label)
    return best, ([best_label] if best_label else []) + gifts


def build_basket(store: Store, people: int, lines: list[tuple[Item, int]], fees_cfg: dict) -> Basket:
    subtotal, promos = 0.0, []
    for it, qty in lines:
        cost, free = item_cost(it, qty)
        subtotal += cost
        if free:
            promos.append(f"{it.bogo_buy} acheté = {it.bogo_get} offert : {it.title}")
        if it.original_price and it.original_price > it.price:
            pct = round(100 * (1 - it.price / it.original_price))
            promos.append(f"-{pct} % sur {it.title}")
    subtotal = round(subtotal, 2)
    discount, threshold_labels = best_threshold_discount(subtotal, store)
    delivery, service, small = compute_fees(subtotal, store, fees_cfg)
    if uber_one_applies(subtotal, fees_cfg):
        promos.append("Uber One : livraison offerte, frais de service réduits")
    elif delivery == 0:
        promos.append("Livraison offerte")
    return Basket(store=store, people=people, lines=list(lines), items_subtotal=subtotal,
                  threshold_discount=discount, delivery_fee=delivery, service_fee=service,
                  small_order_fee=small, promos_used=promos + threshold_labels)


def _unit_lower_bound(it: Item) -> float:
    """Prix unitaire minimal atteignable (un 1+1 divise le prix par deux)."""
    if it.bogo_get and it.bogo_buy:
        return it.price * it.bogo_buy / (it.bogo_buy + it.bogo_get)
    return it.price


def _min_subtotal_lines(items: list[Item], n: int, floor_cents: int, min_distinct: int):
    """Combinaison de `n` unités au sous-total minimal, sous-total >= floor_cents.

    Recherche en profondeur avec élagage (branch & bound) : les articles sont triés
    par prix unitaire minimal croissant, ce qui donne une borne inférieure valable
    pour les unités restantes. Calcul en centimes pour éviter les erreurs d'arrondi.
    """
    m = len(items)
    lb = [round(_unit_lower_bound(it) * 100) for it in items]
    suffix_max = [0] * (m + 1)
    for i in range(m - 1, -1, -1):
        suffix_max[i] = max(round(items[i].price * 100), suffix_max[i + 1])
    cost = [[round(item_cost(it, q)[0] * 100) for q in range(n + 1)] for it in items]
    best = [float("inf"), None]
    chosen: list[tuple[int, int]] = []

    def dfs(i: int, rem: int, partial: int, distinct: int):
        if rem == 0:
            if partial >= floor_cents and distinct >= min_distinct and partial < best[0]:
                best[0], best[1] = partial, list(chosen)
            return
        if i == m or distinct + min(rem, m - i) < min_distinct:
            return
        if partial + rem * suffix_max[i] < floor_cents:
            return
        if max(partial + rem * lb[i], floor_cents) >= best[0]:
            return
        for q in range(rem, -1, -1):
            if q:
                chosen.append((i, q))
            dfs(i + 1, rem - q, partial + cost[i][q], distinct + (q > 0))
            if q:
                chosen.pop()

    dfs(0, n, 0, 0)
    return None if best[1] is None else [(items[i], q) for i, q in best[1]]


def best_basket(store: Store, people: int, classifier: MealClassifier, fees_cfg: dict,
                min_distinct: int = 1, item_filter=None) -> Basket | None:
    """Panier le moins cher (frais et promos compris) de `people` plats principaux.

    Le coût total ne dépend que du sous-total des articles et croît avec lui, sauf
    aux seuils (promo "dès Y €", livraison offerte dès X €, fin des frais de petite
    commande, minimum Uber One). Pour chaque seuil, on cherche le plus petit sous-total qui l'atteint ;
    le meilleur de ces candidats est l'optimum exact. `item_filter(item)` restreint les plats
    autorisés (types de repas).
    """
    mains = sorted((it for it in store.items if classifier.is_main(it) and (item_filter is None or item_filter(it))),
                   key=_unit_lower_bound)
    min_distinct = min(min_distinct, people)
    if len(mains) < min_distinct or not mains:
        return None
    candidates = mains[:MAX_CANDIDATES] + [it for it in mains[MAX_CANDIDATES:] if it.bogo_get]

    floors = {0, fees_cfg["small_order_threshold"], *(p.min_subtotal for p in store.threshold_promos)}
    if store.free_delivery_min is not None:
        floors.add(store.free_delivery_min)
    if (fees_cfg.get("uber_one") or {}).get("enabled"):
        floors.add(fees_cfg["uber_one"]["min_subtotal"])
    best = None
    for floor in sorted(floors):
        lines = _min_subtotal_lines(candidates, people, round(floor * 100), min_distinct)
        if lines is None:
            continue
        b = build_basket(store, people, lines, fees_cfg)
        if best is None or b.total < best.total:
            best = b
    return best


def score_basket(b: Basket, cfg: dict) -> float:
    s, sel = cfg["scoring"], cfg["selection"]
    price = 1 - b.per_person / sel["max_price_per_person"]
    rating = (b.store.rating or 4.0) / 5
    max_eta = cfg["prefilter"]["max_eta_min"]
    eta_mid = ((b.store.eta_min or max_eta) + (b.store.eta_max or max_eta)) / 2
    eta = max(0.0, 1 - eta_mid / max_eta)
    return round(s["w_price"] * price + s["w_rating"] * rating + s["w_eta"] * eta, 4)


def brand_key(title: str) -> str:
    """"Burger King - Aix Jas de Bouffan" et "Burger King" -> "burger king"."""
    t = unicodedata.normalize("NFKD", title.split(" - ")[0]).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t.lower()).split())


def _composition_key(b: Basket) -> tuple:
    return tuple(sorted((it.title.lower(), q) for it, q in b.lines)) + (b.items_subtotal,)


def select_deals(stores: list[Store], cfg: dict, group_sizes: list[int] | None = None,
                 when: datetime | None = None, now: datetime | None = None,
                 meal_types: list[str] | None = None, sort: str = "score") -> dict[int, list[Basket]]:
    """Top N par taille de groupe. `when` : heure de livraison visée (None = maintenant).
    `meal_types` : ne garder que les plats de ces types (None ou vide = tous).
    `sort` : "score" (prix, note et délai pondérés) ou "rating" (mieux noté d'abord)."""
    classifier = MealClassifier(cfg["meal"])
    types = MealTypes(cfg.get("meal_types") or {}) if meal_types else None
    wanted = set(meal_types or [])
    sel = cfg["selection"]
    now = now or datetime.now(PARIS)
    available = {s.uuid: label for s in stores if (label := availability(s, when, now)) is not None}
    result = {}
    for people in group_sizes or sel["group_sizes"]:
        baskets = []
        for store in stores:
            if store.uuid not in available:
                continue
            item_filter = (lambda it, s=store: bool(wanted.intersection(types.of(it, s)))) if types else None
            b = best_basket(store, people, classifier, cfg["fees"], sel.get("min_distinct_items", 1), item_filter)
            if b and b.per_person < sel["max_price_per_person"]:
                b.score = score_basket(b, cfg)
                b.availability = available[store.uuid]
                baskets.append(b)
        if sort == "rating":
            # Mieux noté d'abord, à 0,1 près comme affiché (sans note : à la fin), puis le moins cher.
            baskets.sort(key=lambda b: (-round(b.store.rating or 0, 1), b.per_person))
        else:
            baskets.sort(key=lambda b: b.score, reverse=True)
        # Une même enseigne (ou un même panier sous deux noms) n'apparaît qu'une fois.
        kept, seen = [], set()
        for b in baskets:
            keys = {("brand", brand_key(b.store.title)), ("compo", _composition_key(b))}
            if keys & seen:
                continue
            seen |= keys
            kept.append(b)
        result[people] = kept[: sel["top_n"]]
    return result
