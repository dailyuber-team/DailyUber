import random
import time
from itertools import combinations_with_replacement

import pytest

from dailyuber.models import Item, Store, ThresholdPromo
from dailyuber.parser import parse_delivery_fee, parse_threshold_promos
from dailyuber.selection import (MealClassifier, best_basket, best_threshold_discount, brand_key, build_basket,
                                 compute_fees, item_cost, select_deals)

FEES = {"default_delivery_fee": 2.49, "service_fee_pct": 0.10, "service_fee_min": 0.99,
        "service_fee_max": 4.99, "small_order_threshold": 8.0, "small_order_fee": 2.0}
MEAL = {"min_item_price": 5.0, "main_keywords": ["burger", "menu", "plat", "pizza"],
        "exclude_keywords": ["boisson", "dessert", "frite", "eau", "sauce"]}
CFG = {"fees": FEES, "meal": MEAL,
       "selection": {"group_sizes": [2, 4], "max_price_per_person": 10.0, "top_n": 5},
       "scoring": {"w_price": 0.6, "w_rating": 0.25, "w_eta": 0.15},
       "prefilter": {"max_eta_min": 45}}


def store(items, **kw):
    return Store(uuid=kw.pop("uuid", "s1"), title=kw.pop("title", "Resto"), url="u",
                 rating=4.5, eta_min=15, eta_max=30, items=items, **kw)


# --- Coût des articles et "1 acheté = 1 offert" ---

def test_item_cost_without_promo():
    assert item_cost(Item("Burger", 8.0), 3) == (24.0, 0)


def test_item_cost_bogo():
    it = Item("Burger", 8.0, bogo_buy=1, bogo_get=1)
    assert item_cost(it, 2) == (8.0, 1)
    assert item_cost(it, 3) == (16.0, 1)
    assert item_cost(it, 4) == (16.0, 2)


def test_item_cost_bogo_respects_max_redemption():
    it = Item("Burger", 8.0, bogo_buy=1, bogo_get=1, bogo_max=1)
    assert item_cost(it, 4) == (24.0, 1)


# --- Frais ---

def test_fees_default_delivery_and_service_pct():
    assert compute_fees(30.0, store([]), FEES) == (2.49, 3.0, 0.0)


def test_fees_service_min_max_and_small_order():
    assert compute_fees(5.0, store([]), FEES) == (2.49, 0.99, 2.0)
    assert compute_fees(100.0, store([]), FEES) == (2.49, 4.99, 0.0)


def test_fees_known_delivery_fee_overrides_default():
    assert compute_fees(20.0, store([], delivery_fee=0.0), FEES)[0] == 0.0


# --- Promos à seuil ---

def test_threshold_discount_applies_best_only():
    s = store([], threshold_promos=[ThresholdPromo(20, pct_off=0.2, label="-20%"),
                                    ThresholdPromo(30, amount_off=10, label="-10€")])
    assert best_threshold_discount(19.99, s) == (0.0, [])
    assert best_threshold_discount(25.0, s) == (5.0, ["-20%"])
    assert best_threshold_discount(40.0, s) == (10.0, ["-10€"])


def test_parse_threshold_promos():
    p = parse_threshold_promos(["-20% dès 20 € d'achat", "5 € de réduction dès 25 €",
                                "Article offert dès 16 € d'achat", "1 acheté = 1 offert"])
    assert [(x.min_subtotal, x.pct_off, x.amount_off) for x in p] == [(20, 0.2, 0), (25, 0, 5), (16, 0, 0)]


def test_parse_delivery_fee_ignores_new_user_offer():
    assert parse_delivery_fee(["Frais de livraison à 0.00 € (nouveaux utilisateurs)"]) == (None, None)
    assert parse_delivery_fee(["Frais de livraison\xa0: 1.49 €"]) == (1.49, None)
    assert parse_delivery_fee(["Livraison offerte"]) == (0.0, None)


def test_parse_conditional_free_delivery():
    texts = ["Frais de livraison à\xa00\xa0€ sur les commandes supérieures à\xa015\xa0€"]
    assert parse_delivery_fee(texts) == (None, 15.0)


def test_fees_conditional_free_delivery():
    s = store([], free_delivery_min=15.0)
    assert compute_fees(14.99, s, FEES)[0] == 2.49
    assert compute_fees(15.0, s, FEES)[0] == 0.0


# --- Définition d'un repas ---

@pytest.mark.parametrize("title,section,price,expected", [
    ("Cheese burger", "Burgers", 9.0, True),
    ("Menu burger frites", "Menus", 12.0, True),     # le titre prime sur "frite"
    ("Frites", "Accompagnements", 3.5, False),
    ("Grandes frites cheddar", "Burgers", 6.0, False),
    ("Coca-Cola", "Boissons", 2.5, False),
    ("Plateau 18 pièces", "Plateaux", 14.0, True),    # "eau" (exclu) ne matche pas en milieu de mot
    ("Tiramisu", "Desserts", 5.5, False),
    ("Pizza riz", "Articles en vedette / Sauces", 7.0, False),  # la section exclut
    ("Pizza reine", "Articles en vedette / Pizzas", 9.0, True),
    ("Burger enfant", "Burgers", 4.0, False),        # sous le prix minimum
])
def test_meal_classifier(title, section, price, expected):
    assert MealClassifier(MEAL).is_main(Item(title, price, section=section)) is expected


# --- Composition du panier ---

def test_best_basket_uses_bogo_when_cheaper():
    items = [Item("Burger classique", 7.0, section="Burgers"),
             Item("Burger double", 12.0, section="Burgers", bogo_buy=1, bogo_get=1),
             Item("Coca", 2.0, section="Boissons")]
    b = best_basket(store(items), 2, MealClassifier(MEAL), FEES)
    # 2 × classique = 14 € ; 1+1 double = 12 € -> le 1+1 gagne
    assert [(i.title, q) for i, q in b.lines] == [("Burger double", 2)]
    assert b.items_subtotal == 12.0
    assert b.total == round(12.0 + 2.49 + 1.2, 2)
    assert b.per_person == round(b.total / 2, 2)


def test_best_basket_never_counts_drinks_as_meals():
    items = [Item("Coca", 6.0, section="Boissons"), Item("Pizza reine", 11.0, section="Pizzas")]
    b = best_basket(store(items), 2, MealClassifier(MEAL), FEES)
    assert [(i.title, q) for i, q in b.lines] == [("Pizza reine", 2)]


def test_best_basket_reaches_threshold_when_it_pays_off():
    # 2 × 9,90 = 19,80 (pas de remise) vs 9,90 + 10,50 = 20,40 -> -20 % = 16,32
    items = [Item("Burger A", 9.9, section="Burgers"), Item("Burger B", 10.5, section="Burgers")]
    s = store(items, threshold_promos=[ThresholdPromo(20, pct_off=0.2, label="-20% dès 20 €")])
    b = best_basket(s, 2, MealClassifier(MEAL), FEES)
    assert b.threshold_discount == pytest.approx(4.08)
    assert "-20% dès 20 €" in b.promos_used


def test_select_deals_filters_price_and_closed_stores():
    cheap = store([Item("Burger", 6.0, section="Burgers")], uuid="cheap", title="Cheap")
    pricey = store([Item("Burger", 15.0, section="Burgers")], uuid="pricey", title="Pricey")
    closed = store([Item("Burger", 5.0, section="Burgers")], uuid="closed", title="Closed", is_open=False)
    deals = select_deals([cheap, pricey, closed], CFG)
    assert [b.store.uuid for b in deals[2]] == ["cheap"]
    assert [b.store.uuid for b in deals[4]] == ["cheap"]
    assert all(b.per_person < 10 for b in deals[2] + deals[4])


def test_select_deals_ranks_cheaper_first_at_equal_rating():
    a = store([Item("Burger", 6.0, section="Burgers")], uuid="a", title="A")
    b = store([Item("Burger", 7.5, section="Burgers")], uuid="b", title="B")
    deals = select_deals([b, a], CFG)
    assert [x.store.uuid for x in deals[2]] == ["a", "b"]


# --- Phase 1 : 1 à 6 personnes, algorithme exact ---

def _brute_force_total(s: Store, people: int, min_distinct: int = 1) -> float | None:
    """Référence : toutes les combinaisons possibles (lent, seulement pour les tests)."""
    clf = MealClassifier(MEAL)
    mains = [it for it in s.items if clf.is_main(it)]
    best = None
    for combo in combinations_with_replacement(mains, people):
        lines = {}
        for it in combo:
            lines[id(it)] = (it, lines.get(id(it), (it, 0))[1] + 1)
        if len(lines) < min(min_distinct, people):
            continue
        total = build_basket(s, people, list(lines.values()), FEES).total
        best = total if best is None else min(best, total)
    return best


def _random_store(rng: random.Random) -> Store:
    items = []
    for i in range(rng.randint(1, 7)):
        bogo = rng.random() < 0.3
        items.append(Item(f"Burger {i}", round(rng.uniform(5, 16), 1), section="Burgers",
                          bogo_buy=1 if bogo else 0, bogo_get=1 if bogo else 0,
                          bogo_max=rng.choice([0, 1, 2]) if bogo else 0))
    promos = [ThresholdPromo(rng.choice([15, 20, 25, 30, 40]), pct_off=rng.choice([0.1, 0.2, 0.3]), label="%")
              for _ in range(rng.randint(0, 1))]
    promos += [ThresholdPromo(rng.choice([20, 30]), amount_off=rng.choice([3, 5, 8]), label="€")
               for _ in range(rng.randint(0, 1))]
    return store(items, threshold_promos=promos,
                 free_delivery_min=rng.choice([None, 15.0, 25.0]),
                 delivery_fee=rng.choice([None, 0.99]))


@pytest.mark.parametrize("people", [1, 2, 3, 4, 5, 6])
def test_best_basket_matches_brute_force(people):
    rng = random.Random(people)
    for _ in range(50):
        s = _random_store(rng)
        min_distinct = rng.choice([1, 1, 2, 3])
        b = best_basket(s, people, MealClassifier(MEAL), FEES, min_distinct)
        expected = _brute_force_total(s, people, min_distinct)
        assert (b.total if b else None) == expected


def test_one_person_pays_small_order_fee_when_under_threshold():
    b = best_basket(store([Item("Burger", 7.0, section="Burgers")]), 1, MealClassifier(MEAL), FEES)
    assert b.small_order_fee == 2.0 and b.per_person == round(7.0 + 2.49 + 0.99 + 2.0, 2)


def test_min_distinct_items_forces_variety():
    items = [Item("Burger A", 6.0, section="Burgers"), Item("Burger B", 7.0, section="Burgers")]
    b = best_basket(store(items), 4, MealClassifier(MEAL), FEES, min_distinct=2)
    assert sorted((i.title, q) for i, q in b.lines) == [("Burger A", 3), ("Burger B", 1)]
    assert best_basket(store(items), 4, MealClassifier(MEAL), FEES, min_distinct=3) is None


def test_six_people_is_fast_on_large_menu():
    rng = random.Random(0)
    items = [Item(f"Plat {i}", round(rng.uniform(6, 20), 2), section="Plats",
                  bogo_buy=int(i % 7 == 0), bogo_get=int(i % 7 == 0)) for i in range(80)]
    s = store(items, threshold_promos=[ThresholdPromo(30, pct_off=0.2, label="-20%")], free_delivery_min=25.0)
    t0 = time.perf_counter()
    for _ in range(40):  # ~ 40 restaurants analysés par run
        best_basket(s, 6, MealClassifier(MEAL), FEES, min_distinct=3)
    assert time.perf_counter() - t0 < 2.0


# --- Phase 0 : doublons d'enseignes ---

def test_brand_key_normalizes_names():
    assert brand_key("Burger King - Aix Jas de Bouffan") == brand_key("BURGER KING") == "burger king"
    assert brand_key("🍣Enjoy Sushi Aix-en-Provence🍱") == "enjoy sushi aix en provence"


def test_select_deals_keeps_one_basket_per_brand_and_composition():
    bk1 = store([Item("Whopper", 6.0, section="Burgers")], uuid="1", title="Burger King - Centre")
    bk2 = store([Item("Whopper", 6.5, section="Burgers")], uuid="2", title="Burger King - Jas")
    m1 = store([Item("Empanada box", 7.0, section="Menus")], uuid="3", title="Chez Matias")
    m2 = store([Item("Empanada box", 7.0, section="Menus")], uuid="4", title="Chez Matias Empanadas")
    deals = select_deals([bk1, bk2, m1, m2], CFG, [2])
    assert sorted(b.store.uuid for b in deals[2]) == ["1", "3"]


# --- Uber One ---

FEES_ONE = {**FEES, "uber_one": {"enabled": True, "min_subtotal": 12.0, "service_fee_discount": 0.5}}


def test_uber_one_free_delivery_and_half_service_fee():
    assert compute_fees(30.0, store([]), FEES_ONE) == (0.0, 1.5, 0.0)


def test_uber_one_not_applied_under_minimum():
    assert compute_fees(10.0, store([]), FEES_ONE) == (2.49, 1.0, 0.0)


def test_uber_one_disabled_changes_nothing():
    off = {**FEES, "uber_one": {**FEES_ONE["uber_one"], "enabled": False}}
    assert compute_fees(30.0, store([]), off) == compute_fees(30.0, store([]), FEES)


def test_uber_one_basket_reaches_minimum_when_cheaper():
    # 1 personne : 1 burger à 11 € paie livraison + service (14,59 €) ;
    # le menu à 12 € atteint le minimum Uber One et revient moins cher (12,60 €).
    s = store([Item("Burger", 11.0), Item("Menu burger", 12.0)])
    b = best_basket(s, 1, MealClassifier(MEAL), FEES_ONE)
    assert [it.title for it, _ in b.lines] == ["Menu burger"]
    assert b.total == 12.6
    assert any("Uber One" in p for p in b.promos_used)


# --- Types de repas ---

TYPES = {"burger": {"label": "Burger", "keywords": ["burger"], "cuisines": ["Burgers"]},
         "crousty": {"label": "Crousty", "keywords": ["crousty"], "cuisines": []},
         "pizza": {"label": "Pizza", "keywords": ["pizza"], "cuisines": ["Pizzas", "Italienne"]}}


def test_meal_type_from_item_keywords_first():
    from dailyuber.selection import MealTypes
    mt = MealTypes(TYPES)
    s = store([], title="Tasty Crousty", cuisines=["Pizzas"])
    assert mt.of(Item("Burger crousty", 9.0), s) == ["burger", "crousty"]
    assert mt.of(Item("Plat", 9.0, section="Nos pizzas"), s) == ["pizza"]


def test_meal_type_falls_back_on_store_name_and_cuisines():
    from dailyuber.selection import MealTypes
    mt = MealTypes(TYPES)
    assert mt.of(Item("Menu classique", 9.0), store([], title="Tasty Crousty")) == ["crousty"]
    assert mt.of(Item("Menu du jour", 9.0), store([], cuisines=["Italienne\xa0"])) == ["pizza"]
    assert mt.of(Item("Menu du jour", 9.0), store([])) == []


def test_select_deals_keeps_only_wanted_meal_types():
    cfg = {**CFG, "meal_types": TYPES}
    s1 = store([Item("Menu burger", 6.5)], uuid="a", title="Burger House")
    s2 = store([Item("Plat crousty", 6.8)], uuid="b", title="Tasty Crousty")
    s3 = store([Item("Menu", 6.0)], uuid="c", title="Chez Toto")
    now = __import__("datetime").datetime(2026, 10, 22, 12, 0, tzinfo=__import__("dailyuber.hours", fromlist=["PARIS"]).PARIS)
    pick = lambda types: [b.store.uuid for b in select_deals([s1, s2, s3], cfg, [2], now=now, meal_types=types)[2]]
    assert sorted(pick(None)) == ["a", "b", "c"]
    assert pick(["crousty"]) == ["b"]
    assert sorted(pick(["crousty", "burger"])) == ["a", "b"]


def test_prefilter_max_stores_zero_keeps_all():
    from dailyuber.engine import prefilter
    stores = [store([], uuid=str(i), title=f"R{i}") for i in range(7)]
    cfg = {"prefilter": {"min_rating": 4.0, "max_eta_min": 45}, "fetch": {"max_stores": 0}}
    assert len(prefilter(stores, cfg)) == 7
    cfg["fetch"]["max_stores"] = 3
    assert len(prefilter(stores, cfg)) == 3


def test_always_excluded_keywords_beat_main_keywords():
    meal = {**MEAL, "main_keywords": MEAL["main_keywords"] + ["crousty"], "always_exclude_keywords": ["nutella"]}
    c = MealClassifier(meal)
    assert not c.is_main(Item("Crousty Nutella Banane", 6.5))
    assert c.is_main(Item("Crousty poulet", 8.0))
    assert MealClassifier(MEAL).is_main(Item("Menu burger frites", 9.0))  # sans la liste : inchangé
