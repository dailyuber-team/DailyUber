"""Moteur réutilisable (CLI, future API) : récupération en cache puis sélection instantanée.

La récupération (plusieurs minutes) ne dépend ni du nombre de personnes ni des
réglages de sélection : on la fait une fois, on la met en cache, et toute
nouvelle sélection repart du cache sans requête vers Uber Eats.
"""
import gzip
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from .models import Basket, Store
from .parser import parse_feed_store, parse_store_details
from .selection import select_deals

log = logging.getLogger(__name__)


def _raw_path(cfg: dict) -> Path:
    return Path(cfg["storage"]["data_dir"]) / "raw" / "latest.json.gz"


def prefilter(stores: list[Store], cfg: dict) -> list[Store]:
    p = cfg["prefilter"]
    kept = [s for s in stores
            if (s.rating is None or s.rating >= p["min_rating"])
            and (s.eta_max is None or s.eta_max <= p["max_eta_min"])]
    # Les restaurants avec une promo affichée passent en premier, puis par note.
    kept.sort(key=lambda s: (not s.signposts, -(s.rating or 0)))
    return kept[: cfg["fetch"]["max_stores"]]


def fetch_raw(cfg: dict, progress=None) -> dict:
    """Récupère flux + menus. `progress(done, total, label)` est appelé après chaque menu."""
    from .fetcher import UberEatsSession  # import tardif : le mode cache ne nécessite pas Playwright

    raw = {"fetched_at": datetime.now().isoformat(timespec="seconds"),
           "address": cfg["address"], "feed": [], "stores": {}}
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
            if progress:
                progress(i, len(targets), s.title)
    if not raw["stores"]:
        raise RuntimeError("Aucun menu récupéré")
    path = _raw_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(raw).encode()))
    return raw


def load_cached_raw(cfg: dict, max_age_min: float | None = None) -> dict | None:
    """Dernière récupération, ou None si absente / plus vieille que `max_age_min`."""
    path = _raw_path(cfg)
    if not path.exists():
        return None
    raw = json.loads(gzip.decompress(path.read_bytes()))
    if max_age_min is not None:
        fetched_at = datetime.fromisoformat(raw.get("fetched_at", "1970-01-01T00:00:00"))
        if datetime.now() - fetched_at > timedelta(minutes=max_age_min):
            return None
    return raw


def get_raw(cfg: dict, refresh: bool = False, progress=None) -> dict:
    if not refresh:
        raw = load_cached_raw(cfg, cfg["fetch"]["cache_max_age_min"])
        if raw:
            log.info("Données en cache du %s", raw.get("fetched_at", "?"))
            return raw
    return fetch_raw(cfg, progress)


def build_stores(raw: dict) -> list[Store]:
    feed = {s.uuid: s for s in (parse_feed_store(r) for r in raw["feed"])}
    return [parse_store_details(feed[uuid], data) for uuid, data in raw["stores"].items() if uuid in feed]


def select(stores: list[Store], cfg: dict, people: list[int] | None = None,
           when: datetime | None = None) -> dict[int, list[Basket]]:
    return select_deals(stores, cfg, people or cfg["selection"]["group_sizes"], when)
