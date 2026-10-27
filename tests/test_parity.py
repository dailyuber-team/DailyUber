"""Parité entre le calcul Python (selection.py, hours.py) et celui de l'appli (web/engine.js).

Les mêmes cas (personnes, Uber One, budget, types de repas, heure) sont calculés des deux
côtés, sur des restaurants générés aléatoirement et, s'il y en a, sur la dernière récupération
réelle (data/raw). Les offres doivent être identiques au centime près. Nécessite Node.js.
"""
import copy
import json
import random
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from dailyuber import engine
from dailyuber.config import ROOT, load_config
from dailyuber.export import export_stores, paris_fetched_at
from dailyuber.hours import PARIS
from dailyuber.models import Item, Store, ThresholdPromo
from dailyuber.selection import select_deals

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js absent")

DISHES = ["Burger", "Smash burger", "Pizza", "Margherita", "Menu sushi", "Maki saumon", "Poké bowl",
          "Tacos", "Kebab", "Sandwich", "Wrap", "Salade", "Pâtes carbonara", "Tenders", "Bo bun", "Plat du jour"]
CUISINES = ["Burgers", "Pizzas", "Italienne", "Japonaise", "Sushis", "Asiatique", "Poulet", "Sandwich", "Halal"]


def random_store(rng: random.Random, i: int, fetched_at: datetime) -> Store:
    items = []
    for j in range(rng.randint(1, 30)):
        price = round(rng.uniform(5.5, 17), 2) if rng.random() < 0.7 else rng.choice([6.0, 7.5, 8.9, 9.95, 12.0])
        bogo = rng.random() < 0.2
        items.append(Item(
            title=f"{rng.choice(DISHES)} {j}", price=price, section=rng.choice(["", "Menus", "Nos plats", "Desserts"]),
            original_price=round(price * rng.uniform(1.1, 1.6), 2) if rng.random() < 0.15 else None,
            bogo_buy=1 if bogo else 0, bogo_get=1 if bogo else 0, bogo_max=rng.choice([0, 0, 1, 2]) if bogo else 0,
            has_customizations=rng.random() < 0.5))
    promos = []
    for _ in range(rng.choice([0, 0, 1, 2])):
        if rng.random() < 0.6:
            pct = rng.choice([10, 15, 20, 30, 45])
            m = rng.choice([15, 20, 25, 30])
            promos.append(ThresholdPromo(m, pct_off=pct / 100, label=f"-{pct}% dès {m} €"))
        else:
            a, m = rng.choice([(3, 15), (5, 25), (0, 16)])
            promos.append(ThresholdPromo(m, amount_off=a, label=f"-{a} € dès {m} €"))
    hours = None
    if rng.random() < 0.85:
        hours = {d: [(rng.choice([600, 660, 690]), rng.choice([840, 870, 900]))] for d in range(7)}
        for d in range(7):
            if rng.random() < 0.7:
                hours[d].append((rng.choice([1080, 1110]), rng.choice([1320, 1380, 1440])))
    slots = {}
    if rng.random() < 0.7:
        for k in range(rng.randint(1, 4)):
            day = (fetched_at + timedelta(days=k)).date().isoformat()
            slots[day] = [(s, s + 30) for s in range(rng.choice([660, 690, 720]), rng.choice([1200, 1350, 1410]), 15)
                          if rng.random() < 0.9]
    eta = rng.choice([None, 10, 15, 25])
    return Store(
        uuid=f"s{i}", title=rng.choice([f"Resto {i}", f"Chaîne {i % 5} - Aix", f"Café {i}"]), url=f"u{i}",
        rating=rng.choice([None, 3.9, 4.2, 4.5, 4.8]), rating_count=rng.randint(0, 3000),
        eta_min=eta, eta_max=None if eta is None else eta + rng.choice([0, 10, 20]),
        delivery_fee=rng.choice([None, None, 0.0, 0.99, 1.49]), free_delivery_min=rng.choice([None, None, 15.0, 20.0]),
        threshold_promos=promos, is_open=rng.random() < 0.8, open_until=rng.choice(["", "Ouvert jusqu'à 22:00", "Ouvre à 18:00"]),
        hours=hours, delivery_slots=slots, cuisines=rng.sample(CUISINES, rng.randint(0, 2)), items=items,
        platform=rng.choice(["uber", "uber", "deliveroo"]), min_order=rng.choice([None, None, 10.0, 15.0]))


def cases_for(fetched_at: datetime, rng: random.Random, n: int) -> list[dict]:
    tomorrow = (fetched_at + timedelta(days=1)).date()
    whens = [None, fetched_at + timedelta(minutes=20), fetched_at + timedelta(hours=2, minutes=15),
             datetime(tomorrow.year, tomorrow.month, tomorrow.day, 12, 30, tzinfo=PARIS),
             datetime(tomorrow.year, tomorrow.month, tomorrow.day, 21, 0, tzinfo=PARIS)]
    types = [None, ["pizza"], ["sushi", "poke"], ["burger", "poulet"], ["salade"]]
    return [{"people": rng.randint(1, 6), "uber_one": rng.random() < 0.5, "budget": rng.choice([8.0, 10.0, 12.5, 15.0]),
             "meal_types": rng.choice(types), "top_n": rng.choice([5, 10]), "sort": rng.choice(["score", "rating"]),
             "when": (w := rng.choice(whens)) and w.isoformat()} for _ in range(n)]


def python_deals(stores: list[Store], cfg: dict, fetched_at: datetime, c: dict) -> list[dict]:
    cfg = copy.deepcopy(cfg)
    cfg["fees"]["uber_one"]["enabled"] = c["uber_one"]
    cfg["selection"]["max_price_per_person"] = c["budget"]
    cfg["selection"]["top_n"] = c["top_n"]
    when = datetime.fromisoformat(c["when"]) if c["when"] else None
    deals = select_deals(stores, cfg, [c["people"]], when, now=fetched_at, meal_types=c["meal_types"], sort=c["sort"])
    return [{"uuid": b.store.uuid, "lines": [[it.title, q] for it, q in b.lines],
             "items_subtotal": b.items_subtotal, "threshold_discount": b.threshold_discount,
             "delivery_fee": b.delivery_fee, "service_fee": b.service_fee, "small_order_fee": b.small_order_fee,
             "total": b.total, "per_person": b.per_person, "score": b.score,
             "promos": b.promos_used, "availability": b.availability} for b in deals[c["people"]]]


def run_node(tmp_path: Path, data: dict, cases: list[dict], rounding: list) -> dict:
    (tmp_path / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps({"cases": cases, "rounding": rounding}), encoding="utf-8")
    out = subprocess.run([NODE, str(ROOT / "tests" / "parity.mjs"), str(tmp_path / "data.json"),
                          str(tmp_path / "cases.json")], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def assert_same(stores, cfg, fetched_at, cases, js_deals):
    for c, js in zip(cases, js_deals):
        py = python_deals(stores, cfg, fetched_at, c)
        assert js == py, f"Différence pour {c}"


def test_python_round_is_reproduced():
    rng = random.Random(1)
    values = [rng.uniform(0, 60) for _ in range(2000)] + [k / 1000 for k in range(0, 20000, 5)] + [0.125, 2.675, 1.005]
    rounding = [[x, n] for x in values for n in (0, 2, 4)] + [[x * 100, 0] for x in values]
    js = run_node(Path(__import__("tempfile").mkdtemp()), {"config": {}, "stores": [], "fetched_at": ""}, [], rounding)
    assert js["rounded"] == [round(x, n) for x, n in rounding]


def test_parity_on_random_stores(tmp_path):
    rng = random.Random(42)
    cfg = load_config()
    fetched_at = datetime(2026, 10, 22, 10, 5, tzinfo=PARIS)
    stores = [random_store(rng, i, fetched_at) for i in range(60)]
    cases = cases_for(fetched_at, rng, 150)
    js = run_node(tmp_path, export_stores(stores, cfg, fetched_at), cases, [])
    assert_same(stores, cfg, fetched_at, cases, js["deals"])


def test_parity_on_last_real_fetch(tmp_path):
    cfg = load_config()
    raw = engine.load_cached_raw(cfg)
    if raw is None:
        pytest.skip("pas de récupération réelle dans data/raw")
    stores, fetched_at = engine.build_stores(raw), paris_fetched_at(raw)
    cases = cases_for(fetched_at, random.Random(7), 80)
    js = run_node(tmp_path, export_stores(stores, cfg, fetched_at), cases, [])
    assert_same(stores, cfg, fetched_at, cases, js["deals"])
