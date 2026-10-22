"""Composition des paniers, calcul des frais et des promos, filtrage et classement."""
import re
from itertools import combinations_with_replacement

from .models import Basket, Item, Store

MAX_CANDIDATES = 20  # plats les moins chers considérés par restaurant (plus tous les 1+1)


def _kw_regex(keywords: list[str]) -> re.Pattern:
    # Ancré en début de mot : "eau" ne matche pas "plateau", "frite" matche "frites".
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in keywords) + ")", re.I)


class MealClassifier:
    def __init__(self, meal_cfg: dict):
        self.min_price = meal_cfg["min_item_price"]
        self.main = _kw_regex(meal_cfg["main_keywords"])
        self.exclude = _kw_regex(meal_cfg["exclude_keywords"])

    def is_main(self, item: Item) -> bool:
        if item.price < self.min_price:
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


def item_cost(item: Item, qty: int) -> tuple[float, int]:
    """Coût de `qty` unités d'un article, et nombre d'unités offertes ("X achetés = Y offerts")."""
    free = 0
    if item.bogo_get and item.bogo_buy:
        groups = qty // (item.bogo_buy + item.bogo_get)
        if item.bogo_max:
            groups = min(groups, item.bogo_max)
        free = groups * item.bogo_get
    return round(item.price * (qty - free), 2), free


def compute_fees(items_subtotal: float, store: Store, fees_cfg: dict) -> tuple[float, float, float]:
    """(frais de livraison, frais de service, frais de petite commande)."""
    delivery = store.delivery_fee if store.delivery_fee is not None else fees_cfg["default_delivery_fee"]
    if store.free_delivery_min is not None and items_subtotal >= store.free_delivery_min:
        delivery = 0.0
    service = min(max(items_subtotal * fees_cfg["service_fee_pct"], fees_cfg["service_fee_min"]),
                  fees_cfg["service_fee_max"])
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


def build_basket(store: Store, people: int, combo: tuple[Item, ...], fees_cfg: dict) -> Basket:
    counts: dict[int, int] = {}
    for it in combo:
        counts[id(it)] = counts.get(id(it), 0) + 1
    by_id = {id(it): it for it in combo}
    subtotal, promos, lines = 0.0, [], []
    for iid, qty in counts.items():
        it = by_id[iid]
        cost, free = item_cost(it, qty)
        subtotal += cost
        lines.append((it, qty))
        if free:
            promos.append(f"{it.bogo_buy} acheté = {it.bogo_get} offert : {it.title}")
        if it.original_price and it.original_price > it.price:
            pct = round(100 * (1 - it.price / it.original_price))
            promos.append(f"-{pct} % sur {it.title}")
    subtotal = round(subtotal, 2)
    discount, threshold_labels = best_threshold_discount(subtotal, store)
    delivery, service, small = compute_fees(subtotal, store, fees_cfg)
    if delivery == 0:
        promos.append("Livraison offerte")
    return Basket(store=store, people=people, lines=lines, items_subtotal=subtotal,
                  threshold_discount=discount, delivery_fee=delivery, service_fee=service,
                  small_order_fee=small, promos_used=promos + threshold_labels)


def best_basket(store: Store, people: int, classifier: MealClassifier, fees_cfg: dict) -> Basket | None:
    """Panier le moins cher de `people` plats principaux (répétitions autorisées)."""
    mains = [it for it in store.items if classifier.is_main(it)]
    if not mains:
        return None

    def unit_price(it: Item) -> float:
        cost, _ = item_cost(it, it.bogo_buy + it.bogo_get) if it.bogo_get else (it.price, 0)
        return cost / (it.bogo_buy + it.bogo_get) if it.bogo_get else cost

    candidates = sorted(mains, key=unit_price)[:MAX_CANDIDATES]
    candidates += [it for it in mains if it.bogo_get and it not in candidates]

    best = None
    for combo in combinations_with_replacement(candidates, people):
        b = build_basket(store, people, combo, fees_cfg)
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


def select_deals(stores: list[Store], cfg: dict) -> dict[int, list[Basket]]:
    classifier = MealClassifier(cfg["meal"])
    sel = cfg["selection"]
    result = {}
    for people in sel["group_sizes"]:
        baskets = []
        for store in stores:
            if not store.is_open:
                continue
            b = best_basket(store, people, classifier, cfg["fees"])
            if b and b.per_person < sel["max_price_per_person"]:
                b.score = score_basket(b, cfg)
                baskets.append(b)
        baskets.sort(key=lambda b: b.score, reverse=True)
        result[people] = baskets[: sel["top_n"]]
    return result
