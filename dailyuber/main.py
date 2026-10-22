"""Point d'entrée CLI : python -m dailyuber.main [--refresh | --offline] [--people 1 3 6]"""
import argparse
import logging
import sys

from . import engine
from .config import load_config
from .history import mark_new_and_save
from .report import format_report

log = logging.getLogger("dailyuber")


def run(cfg: dict, offline: bool, refresh: bool, people: list[int] | None) -> str:
    if offline:
        raw = engine.load_cached_raw(cfg)
        if raw is None:
            raise RuntimeError("Pas de données en cache : lancer sans --offline")
    else:
        raw = engine.get_raw(cfg, refresh=refresh)
    stores = engine.build_stores(raw)
    deals = engine.select(stores, cfg, people)
    mark_new_and_save(deals, cfg["storage"]["data_dir"])
    stats = {"address": cfg["address"]["label"], "fetched_at": raw.get("fetched_at", "?"),
             "feed_stores": len(raw["feed"]), "fetched": len(stores),
             "open": sum(s.is_open for s in stores)}
    return format_report(deals, stats)


def main() -> int:
    ap = argparse.ArgumentParser(description="Meilleurs deals Uber Eats du jour")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--offline", action="store_true", help="réutilise la dernière récupération, quel que soit son âge")
    src.add_argument("--refresh", action="store_true", help="force une nouvelle récupération même si le cache est récent")
    ap.add_argument("--people", type=int, nargs="+", choices=range(1, 7), metavar="N",
                    help="nombre(s) de personnes, de 1 à 6 (défaut : config.yaml)")
    ap.add_argument("--config", help="chemin vers config.yaml")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config(args.config)
    try:
        print(run(cfg, args.offline, args.refresh, args.people))
    except Exception:
        log.exception("Échec du run")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
