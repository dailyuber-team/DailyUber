"""Point d'entrée : python -m dailyuber.main [--offline]"""
import argparse
import gzip
import json
import logging
import sys
from pathlib import Path

from .config import load_config
from .history import mark_new_and_save
from .models import Store
from .parser import parse_feed_store, parse_store_details
from .report import format_report
from .selection import select_deals

log = logging.getLogger("dailyuber")


def prefilter(stores: list[Store], cfg: dict) -> list[Store]:
    p = cfg["prefilter"]
    kept = [s for s in stores
            if (s.rating is None or s.rating >= p["min_rating"])
            and (s.eta_max is None or s.eta_max <= p["max_eta_min"])]
    # Les restaurants avec une promo affichée passent en premier, puis par note.
    kept.sort(key=lambda s: (not s.signposts, -(s.rating or 0)))
    return kept[: cfg["fetch"]["max_stores"]]


def fetch_raw(cfg: dict) -> dict:
    from .fetcher import UberEatsSession  # import tardif : --offline ne nécessite pas Playwright

    raw = {"feed": [], "stores": {}}
    with UberEatsSession(cfg) as session:
        raw["feed"] = session.fetch_feed()
        targets = prefilter([parse_feed_store(s) for s in raw["feed"]], cfg)
        log.info("%d restaurants retenus pour récupérer le menu", len(targets))
        for i, s in enumerate(targets, 1):
            try:
                raw["stores"][s.uuid] = session.fetch_store(s.uuid)
                log.info("[%d/%d] %s", i, len(targets), s.title)
            except Exception as e:  # un menu en échec ne doit pas faire tomber le run
                log.warning("[%d/%d] %s : échec (%s)", i, len(targets), s.title, e)
    if not raw["stores"]:
        raise RuntimeError("Aucun menu récupéré")
    return raw


def run(cfg: dict, offline: bool) -> str:
    raw_path = Path(cfg["storage"]["data_dir"]) / "raw" / "latest.json.gz"
    if offline:
        raw = json.loads(gzip.decompress(raw_path.read_bytes()))
    else:
        raw = fetch_raw(cfg)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(gzip.compress(json.dumps(raw).encode()))

    feed = {s.uuid: s for s in (parse_feed_store(r) for r in raw["feed"])}
    stores = [parse_store_details(feed[uuid], data) for uuid, data in raw["stores"].items() if uuid in feed]
    deals = select_deals(stores, cfg)
    mark_new_and_save(deals, cfg["storage"]["data_dir"])
    stats = {"address": cfg["address"]["label"], "feed_stores": len(feed),
             "fetched": len(stores), "open": sum(s.is_open for s in stores)}
    return format_report(deals, stats)


def main() -> int:
    ap = argparse.ArgumentParser(description="Meilleurs deals Uber Eats du jour")
    ap.add_argument("--offline", action="store_true", help="réutilise la dernière récupération (data/raw)")
    ap.add_argument("--config", help="chemin vers config.yaml")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config(args.config)
    try:
        print(run(cfg, args.offline))
    except Exception:
        log.exception("Échec du run")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
