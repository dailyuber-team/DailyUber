"""Export des données de l'appli web : python -m dailyuber.export [--refresh | --offline] [--out _site/data.json]

L'appli calcule les offres dans le téléphone (web/engine.js, portage de selection.py et de
hours.availability). On n'exporte que ce dont ce calcul a besoin : les plats principaux
(avec leurs types de repas déjà calculés), les frais, les promos et les horaires.
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import engine
from .config import load_config
from .hours import PARIS
from .models import Store
from .selection import MealClassifier, MealTypes

log = logging.getLogger("dailyuber")
FORMAT_VERSION = 3  # 2 : catalog et favorites ; 3 : lat/lng des restaurants et center


def paris_fetched_at(raw: dict) -> datetime:
    """`fetched_at` est enregistré à l'heure locale de la machine (UTC sur GitHub Actions)."""
    t = datetime.fromisoformat(raw["fetched_at"])
    return t.astimezone(PARIS)  # une date naïve est interprétée comme heure locale


def export_stores(stores: list[Store], cfg: dict, fetched_at: datetime,
                  catalog: list[Store] | None = None, favorites: list[str] | None = None) -> dict:
    """`catalog` : tous les restaurants du flux, exportés en version légère (sans menu) pour
    chercher et afficher un favori. `favorites` : UUID des favoris de config.yaml."""
    classifier, types = MealClassifier(cfg["meal"]), MealTypes(cfg["meal_types"])
    out = []
    for s in stores:
        items = [{"title": it.title, "price": it.price, "original_price": it.original_price,
                  "bogo_buy": it.bogo_buy, "bogo_get": it.bogo_get, "bogo_max": it.bogo_max,
                  "has_customizations": it.has_customizations, "types": types.of(it, s)}
                 for it in s.items if classifier.is_main(it)]
        if not items:  # aucun plat principal : le restaurant ne peut jamais sortir
            continue
        out.append({
            "uuid": s.uuid, "title": s.title, "url": s.url,
            "rating": s.rating, "rating_count": s.rating_count, "eta_min": s.eta_min, "eta_max": s.eta_max,
            "lat": s.lat, "lng": s.lng,
            "delivery_fee": s.delivery_fee, "free_delivery_min": s.free_delivery_min,
            "platform": s.platform, "min_order": s.min_order,
            "threshold_promos": [{"min_subtotal": p.min_subtotal, "pct_off": p.pct_off,
                                  "amount_off": p.amount_off, "label": p.label} for p in s.threshold_promos],
            "is_open": s.is_open, "open_until": s.open_until,
            "hours": None if s.hours is None else {str(d): r for d, r in s.hours.items()},
            "delivery_slots": s.delivery_slots,
            "items": items,
        })
    exported = {s["uuid"] for s in out}
    light = {}
    for s in [*stores, *(catalog or [])]:  # menu récupéré d'abord : infos les plus complètes
        if s.uuid not in exported and s.uuid not in light:
            light[s.uuid] = {"uuid": s.uuid, "title": s.title, "url": s.url, "rating": s.rating,
                             "rating_count": s.rating_count, "eta_min": s.eta_min, "eta_max": s.eta_max,
                             "lat": s.lat, "lng": s.lng}
    sel = cfg["selection"]
    return {
        "version": FORMAT_VERSION,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "address": cfg["address"]["label"],
        "center": {"lat": cfg["address"]["latitude"], "lng": cfg["address"]["longitude"]},
        "config": {
            "fees": cfg["fees"],
            "selection": {"max_price_per_person": sel["max_price_per_person"],
                          "min_distinct_items": sel.get("min_distinct_items", 1)},
            "scoring": cfg["scoring"],
            "prefilter": {"max_eta_min": cfg["prefilter"]["max_eta_min"]},
        },
        "meal_types": [{"id": t, "label": c["label"]} for t, c in cfg["meal_types"].items()],
        "stores": out,
        "catalog": list(light.values()),
        "favorites": favorites or [],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Exporte les données de l'appli web")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--offline", action="store_true", help="réutilise la dernière récupération, quel que soit son âge")
    src.add_argument("--refresh", action="store_true", help="force une nouvelle récupération même si le cache est récent")
    ap.add_argument("--out", default="_site/data.json", help="fichier de sortie (défaut : _site/data.json)")
    ap.add_argument("--max-stores", type=int, metavar="N", help="menus à récupérer, 0 = tous (défaut : config.yaml)")
    ap.add_argument("--config", help="chemin vers config.yaml")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)
    if args.max_stores is not None:
        cfg["fetch"]["max_stores"] = args.max_stores
    try:
        raw = engine.load_raw(cfg, args.offline, args.refresh)
        data = export_stores(engine.build_stores(raw), cfg, paris_fetched_at(raw),
                             engine.build_catalog(raw), raw.get("favorites", []))
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        log.info("%s : %d restaurants, %d Ko", out, len(data["stores"]), out.stat().st_size // 1024)
    except Exception:
        log.exception("Échec de l'export")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
