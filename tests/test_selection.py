import pytest

from dailyuber.models import Item, Store, ThresholdPromo
from dailyuber.parser import parse_delivery_fee, parse_threshold_promos
from dailyuber.selection import (MealClassifier, best_basket, best_threshold_discount,
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
    a = store([Item("Burger", 6.0, section="Burgers")], uuid="a")
    b = store([Item("Burger", 7.5, section="Burgers")], uuid="b")
    deals = select_deals([b, a], CFG)
    assert [x.store.uuid for x in deals[2]] == ["a", "b"]
