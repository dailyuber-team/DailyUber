"""Deliveroo : lecture des pages (format réel, données factices), frais par plateforme, montant minimum."""
from datetime import datetime

import pytest

from dailyuber import deliveroo as D
from dailyuber.hours import PARIS, availability
from dailyuber.models import Item, Store
from dailyuber.selection import MealClassifier, best_basket, compute_fees, platform_fees, select_deals

FEES = {"default_delivery_fee": 2.49, "service_fee_pct": 0.10, "service_fee_min": 0.99, "service_fee_max": 4.99,
        "small_order_threshold": 8.0, "small_order_fee": 2.0,
        "uber_one": {"enabled": True, "min_subtotal": 12.0, "service_fee_discount": 0.5},
        "platforms": {"deliveroo": {"service_fee_pct": 0.05, "service_fee_min": 0.5, "small_order_threshold": 0.0,
                                    "small_order_fee": 0.0, "default_delivery_fee": 1.99}}}
MEAL = {"min_item_price": 5.0, "main_keywords": ["burger", "plat"], "exclude_keywords": ["boisson"]}


def card(rid, name, eta="15", promos=(), rating="4.4", count="(276)"):
    data = {"partner-name.content": name, "partner-rating.content": rating, "partner-rating-count.content": count,
            "home-units-delivery-time.content": eta}
    for i, p in enumerate(promos, 1):
        data[f"promotional-tag-{i}-main-tag-label.content"] = p
    return {"typeName": "UIRooBlock", "rooTemplateId": "partner-card-large-v1-abc", "data": {
        **data, "partner-card.on-tap": {"action": {"name": "navigate_to_restaurant", "parameters": {
            "restaurant_id": rid, "restaurant_name": name,
            "restaurant_href": f"/menu/Aix en Provence/aix-centre/{name.lower()}?day=today&time=ASAP"}}}}}


def listing(*cards):
    return {"props": {"initialState": {"home": {"feed": {"results": {"data": [
        {"typeName": "UILayoutCarousel", "blocks": list(cards)}, {"blocks": [cards[0]]}]}}}}}}  # doublon ignoré


MENU = {"props": {"initialState": {"menuPage": {"menu": {
    "header": {"rows": [{"text": t} for t in ["Burgers", "·", "Vegan", "À 0.31 km", "·", "Ferme à 22:30", "·",
                                                "Montant min. de 10,00 €", "·", "1,79 € de livraison", "4.4 Très bien"]]},
    "metas": {"root": {
        "restaurant": {"id": "1", "name": "Bioburger - Aix", "location": {"address": {"address1": "1 rue X, Aix"}}},
        "categories": [{"id": "c1", "name": "Burgers"}, {"id": "c2", "name": "Boissons"}],
        "items": [
            {"id": "i1", "name": "Le Bacon Cheese", "categoryId": "c1", "available": True, "modifierGroupIds": ["m"],
             "price": {"fractional": 1450}, "priceDiscounted": {"fractional": 0}, "offerText": "1 plat acheté = 1 plat offert"},
            {"id": "i2", "name": "Le Classique", "categoryId": "c1", "available": True,
             "price": {"fractional": 1200}, "priceDiscounted": {"fractional": 960}},
            {"id": "i3", "name": "Le Petit-déj", "categoryId": "c1", "available": False, "price": {"fractional": 800}},
            {"id": "i4", "name": "Coca", "categoryId": "c2", "available": True, "price": {"fractional": 300}}],
        "offer": {"offers": [{"typeName": "BuyOneGetOneFreeOffer", "itemIds": ["i1"]}],
                  "progressBar": {"offer": {"typeName": "FreeDeliveryOffer", "minimumOrderValue": {"fractional": 1500}}}},
    }}}}}}}


def test_parse_listing():
    found = D.parse_listing(listing(card("1", "Bioburger", promos=["1 plat acheté = 1 plat offert"]),
                                    card("2", "Kyoto", eta="", rating="Nouveau", count="")))
    assert list(found) == ["1", "2"]
    assert found["1"] == {"name": "Bioburger", "href": "/menu/Aix en Provence/aix-centre/bioburger", "rating": 4.4,
                          "rating_count": 276, "eta": [15, 15], "promos": ["1 plat acheté = 1 plat offert"]}
    assert (found["2"]["eta"], found["2"]["rating"], found["2"]["rating_count"]) == (None, None, 0)


def test_next_data_missing_raises():
    with pytest.raises(D.DeliverooError):
        D.next_data("<html>Access denied</html>")


@pytest.mark.parametrize("text,expected", [
    ("Commandez pour 15\xa0€, profitez de -‪15\xa0%‬", ([(15.0, 0.15, 0.0)], None)),
    ("Commandez pour 10\xa0€, Économisez la livraison", ([], 10.0)),
    ("Un produit offert dès 30\xa0€ d'achat", ([(30.0, 0.0, 0.0)], None)),
    ("1 plat acheté = 1 plat offert", ([], None)),
])
def test_parse_promos(text, expected):
    promos, free_min = D.parse_promos([text])
    assert ([(p.min_subtotal, p.pct_off, p.amount_off) for p in promos], free_min) == expected


def raw_bioburger():
    now = D.parse_listing(listing(card("1", "Bioburger", promos=["Commandez pour 20 €, profitez de -20 %"])))
    tonight = D.parse_listing(listing(card("1", "Bioburger")))
    return {"listings": {"now": now, "2026-10-27 19:00": tonight, "2026-10-28 12:00": tonight, "2026-10-28 13:00": {}},
            "menus": {"1": D.trim_menu(MENU)}, "geocode": {"1 rue X, Aix": [43.53, 5.45]}}


def test_build_store_from_listing_and_menu():
    s = D.build_stores(raw_bioburger())[0]
    assert (s.uuid, s.platform, s.title) == ("deliveroo:1", "deliveroo", "Bioburger")
    assert s.url == "https://deliveroo.fr/fr/menu/Aix%20en%20Provence/aix-centre/bioburger"
    assert (s.delivery_fee, s.free_delivery_min, s.min_order) == (1.79, 15.0, 10.0)
    assert (s.is_open, s.open_until, s.cuisines, (s.lat, s.lng)) == (True, "Ouvert jusqu'à 22:30", ["Burgers", "Vegan"], (43.53, 5.45))
    assert [(p.min_subtotal, p.pct_off) for p in s.threshold_promos] == [(20.0, 0.2)]
    assert s.delivery_slots == {"2026-10-27": [(1110, 1170)], "2026-10-28": [(690, 750)]}
    by = {i.title: i for i in s.items}
    assert set(by) == {"Le Bacon Cheese", "Le Classique", "Coca"}          # indisponible écarté
    bacon, classique = by["Le Bacon Cheese"], by["Le Classique"]
    assert (bacon.price, bacon.bogo_buy, bacon.bogo_get, bacon.bogo_max, bacon.has_customizations) == (14.5, 1, 1, 1, True)
    assert (classique.price, classique.original_price, classique.section) == (9.6, 12.0, "Burgers")


def test_deliveroo_availability_uses_sampled_slots():
    s = D.build_stores(raw_bioburger())[0]
    fetched = datetime(2026, 10, 27, 10, 0, tzinfo=PARIS)
    assert availability(s, datetime(2026, 10, 27, 19, 20, tzinfo=PARIS), fetched) == "Créneau 18:30–19:30"
    assert availability(s, datetime(2026, 10, 27, 13, 0, tzinfo=PARIS), fetched) is None    # pas livré à 13 h
    assert availability(s, datetime(2026, 10, 30, 12, 0, tzinfo=PARIS), fetched) is None    # hors horizon, pas d'horaires


def test_closed_now_when_not_in_asap_listing():
    raw = raw_bioburger()
    raw["listings"]["now"] = D.parse_listing(listing(card("1", "Bioburger", eta="")))
    s = D.build_stores(raw)[0]
    assert s.is_open is False


def test_geohash_known_value():
    assert D.geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"   # exemple de référence de Wikipédia


def test_sample_keys_skip_past_times():
    cfg = {"deliveroo": {"sample_times": ["12:00", "19:00"]}}
    keys = D.sample_keys(cfg, datetime(2026, 10, 27, 13, 0, tzinfo=PARIS))
    assert [k for k, _, _ in keys] == ["2026-10-27 19:00", "2026-10-28 12:00", "2026-10-28 19:00"]
    assert keys[1][1:] == ("tomorrow", "12:00")


# --- Frais par plateforme et montant minimum ---

def _store(platform, **kw):
    return Store(uuid=platform, title=platform, url="u", rating=4.5, eta_min=10, eta_max=20,
                 platform=platform, **kw)


def test_platform_fees_override_and_no_uber_one_on_deliveroo():
    d = platform_fees(FEES, "deliveroo")
    assert (d["service_fee_pct"], d["default_delivery_fee"], d["uber_one"]["enabled"]) == (0.05, 1.99, False)
    assert platform_fees(FEES, "uber") is FEES
    # 20 € d'articles : Uber One (livraison 0, service -50 %) seulement sur Uber Eats
    assert compute_fees(20.0, _store("uber"), FEES) == (0.0, 1.0, 0.0)
    assert compute_fees(20.0, _store("deliveroo"), FEES) == (1.99, 1.0, 0.0)
    assert compute_fees(5.0, _store("deliveroo"), FEES) == (1.99, 0.5, 0.0)   # pas de petite commande


def test_min_order_forces_basket_above_minimum():
    s = _store("deliveroo", min_order=15.0, items=[Item("Burger", 6.0, section="Burgers"),
                                                   Item("Burger XL", 9.5, section="Burgers")])
    b = best_basket(s, 2, MealClassifier(MEAL), FEES)
    assert b.items_subtotal >= 15.0
    assert sorted((i.title, q) for i, q in b.lines) == [("Burger", 1), ("Burger XL", 1)]
    s.items = [Item("Burger", 6.0, section="Burgers")]
    assert best_basket(s, 2, MealClassifier(MEAL), FEES) is None   # 12 € max : sous le minimum


def test_same_brand_on_both_platforms_is_kept_twice():
    items = [Item("Burger", 6.0, section="Burgers")]
    cfg = {"fees": FEES, "meal": MEAL, "selection": {"group_sizes": [2], "max_price_per_person": 12.0, "top_n": 5},
           "scoring": {"w_price": 0.6, "w_rating": 0.25, "w_eta": 0.15}, "prefilter": {"max_eta_min": 45}}
    uber = Store(uuid="u", title="Burger King", url="u", items=items)
    dr = Store(uuid="deliveroo:1", title="Burger King - Aix", url="d", items=items, platform="deliveroo")
    assert {b.store.uuid for b in select_deals([uber, dr], cfg)[2]} == {"u", "deliveroo:1"}


def test_menu_promos():
    offers = [
        {"typeName": "FullMenuPercentOffOffer", "minimumOrderValue": {"fractional": 1500}, "percentageDiscount": 15, "maxDiscount": None},
        {"typeName": "FullMenuPercentOffOffer", "minimumOrderValue": {"fractional": 0}, "percentageDiscount": 10},   # déjà dans les prix
        {"typeName": "FullMenuPercentOffOffer", "minimumOrderValue": {"fractional": 2000}, "percentageDiscount": 20,
         "maxDiscount": {"fractional": 500}},                                                                          # plafond : ignorée
        {"typeName": "FreeItemOffer", "minimumOrderValue": {"fractional": 3000}, "itemIds": ["x"]},
        {"typeName": "ItemSpecificPercentOffOffer", "minimumOrderValue": {"fractional": 0}, "percentageDiscount": 20},
    ]
    assert [(p.min_subtotal, p.pct_off, p.label) for p in D.menu_promos(offers)] == [
        (15.0, 0.15, "-15 % dès 15 €"), (0.0, 0.10, "-10 % sur tout le menu"), (30.0, 0.0, "Article offert dès 30 €")]


def test_full_menu_discount_is_counted_once_on_subtotal():
    # Constaté le 27/10 (GREY SUSHI) : "-15 % dès 20 €", et chaque plat affiché à -15 %.
    raw = raw_bioburger()
    raw["listings"]["now"]["1"]["promos"] = []
    menu = raw["menus"]["1"]
    menu["offers"] = [{"typeName": "FullMenuPercentOffOffer", "minimumOrderValue": {"fractional": 2000},
                       "percentageDiscount": 15}]
    menu["items"][1]["priceDiscounted"] = {"fractional": 1020}            # 12,00 € affiché 10,20 €
    s = D.build_stores(raw)[0]
    classique = next(i for i in s.items if i.title == "Le Classique")
    assert (classique.price, classique.original_price) == (12.0, None)     # prix plein
    assert [(p.min_subtotal, p.pct_off) for p in s.threshold_promos] == [(20.0, 0.15)]


def test_badge_and_menu_offer_are_not_counted_twice():
    raw = raw_bioburger()
    raw["listings"]["now"]["1"]["promos"] = ["Un produit offert dès 30\xa0€ d'achat"]
    raw["menus"]["1"]["offers"].append({"typeName": "FreeItemOffer", "minimumOrderValue": {"fractional": 3000}})
    s = D.build_stores(raw)[0]
    assert [(p.min_subtotal, p.pct_off) for p in s.threshold_promos] == [(30.0, 0.0)]
