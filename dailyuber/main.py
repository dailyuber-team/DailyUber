"""Point d'entrée CLI : python -m dailyuber.main [--refresh | --offline] [--people 1 3 6] [--at 12:30 [--on demain]] [--uber-one]
                    [--type pizza sushi] [--budget 12]"""
import argparse
import logging
import sys
from datetime import datetime

from . import engine
from .config import load_config
from .history import mark_new_and_save
from .hours import DAYS, PARIS, resolve_target
from .report import format_report

log = logging.getLogger("dailyuber")


def when_label(when: datetime | None) -> str:
    if when is None:
        return "maintenant"
    return f"{DAYS[when.weekday()]} {when:%d/%m} à {when:%H:%M}"


def run(cfg: dict, offline: bool, refresh: bool, people: list[int] | None,
        when: datetime | None = None, uber_one: bool | None = None,
        meal_types: list[str] | None = None, budget: float | None = None) -> str:
    if offline:
        raw = engine.load_cached_raw(cfg)
        if raw is None:
            raise RuntimeError("Pas de données en cache : lancer sans --offline")
    else:
        raw = engine.get_raw(cfg, refresh=refresh)
    stores = engine.build_stores(raw)
    cfg = engine.with_uber_one(cfg, uber_one)
    if budget is not None:
        cfg["selection"]["max_price_per_person"] = budget
    deals = engine.select(stores, cfg, people, when, meal_types=meal_types)
    if when is None:  # l'historique "nouveau depuis la veille" ne suit que les recherches immédiates
        mark_new_and_save(deals, cfg["storage"]["data_dir"])
    stats = {"address": cfg["address"]["label"], "fetched_at": raw.get("fetched_at", "?"),
             "feed_stores": len(raw["feed"]), "fetched": len(stores),
             "open": sum(s.is_open for s in stores), "when": when_label(when),
             "uber_one": cfg["fees"]["uber_one"]["enabled"]}
    return format_report(deals, stats)


def main() -> int:
    ap = argparse.ArgumentParser(description="Meilleurs deals Uber Eats du jour")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--offline", action="store_true", help="réutilise la dernière récupération, quel que soit son âge")
    src.add_argument("--refresh", action="store_true", help="force une nouvelle récupération même si le cache est récent")
    ap.add_argument("--people", type=int, nargs="+", choices=range(1, 7), metavar="N",
                    help="nombre(s) de personnes, de 1 à 6 (défaut : config.yaml)")
    ap.add_argument("--at", metavar="HH:MM", help="heure de livraison visée (défaut : maintenant)")
    ap.add_argument("--on", metavar="DATE", help="jour visé avec --at : AAAA-MM-JJ ou 'demain' (défaut : prochaine occurrence)")
    ap.add_argument("--uber-one", action=argparse.BooleanOptionalAction, default=None,
                    help="calcule les prix avec (ou sans, --no-uber-one) l'abonnement Uber One (défaut : config.yaml)")
    ap.add_argument("--type", nargs="+", metavar="TYPE", dest="meal_types",
                    help="types de repas (meal_types dans config.yaml : burger, pizza, sushi...)")
    ap.add_argument("--budget", type=float, metavar="EUR", help="prix max par personne (défaut : config.yaml)")
    ap.add_argument("--config", help="chemin vers config.yaml")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config(args.config)
    unknown = set(args.meal_types or []) - set(cfg["meal_types"])
    if unknown:
        ap.error(f"type(s) inconnu(s) : {', '.join(sorted(unknown))} (choix : {', '.join(cfg['meal_types'])})")
    try:
        when = resolve_target(args.at, args.on, datetime.now(PARIS))
        print(run(cfg, args.offline, args.refresh, args.people, when, args.uber_one,
                  args.meal_types, args.budget))
    except Exception:
        log.exception("Échec du run")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
