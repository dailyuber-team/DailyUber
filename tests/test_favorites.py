import copy
import gzip
import json
from datetime import datetime
from pathlib import Path

import pytest

from dailyuber import engine, fetcher
from dailyuber.config import load_config
from dailyuber.export import export_stores
from dailyuber.hours import PARIS
from dailyuber.parser import parse_store_only, store_url, store_uuid_from_ref

PEKYO_URL = "https://www.ubereats.com/fr/store/pekyo/o1RM0wMxRn6Xy4keWIR-AA"
PEKYO_UUID = "a3544cd3-0331-467e-97cb-891e58847e00"  # storeUuid réel du flux


def test_store_uuid_from_ref_accepts_link_and_uuid():
    assert store_uuid_from_ref(PEKYO_URL) == PEKYO_UUID
    assert store_uuid_from_ref(PEKYO_URL + "?diningMode=DELIVERY") == PEKYO_UUID
    assert store_uuid_from_ref(PEKYO_UUID.upper()) == PEKYO_UUID
    with pytest.raises(ValueError):
        store_uuid_from_ref("Pekyo")


def test_store_url_round_trip():
    assert store_url("pekyo", PEKYO_UUID) == PEKYO_URL


# --- Données factices au format d'Uber Eats ---

def feed_entry(uuid: str, title: str, rating: float = 4.5, promo: bool = False) -> dict:
    return {"storeUuid": uuid, "title": {"text": title}, "actionUrl": f"/store/{title.lower()}/x",
            "meta": [], "signposts": [{"text": "1 acheté = 1 offert"}] if promo else [],
            "tracking": {"storePayload": {"ratingInfo": {"storeRatingScore": rating, "ratingCount": "100"},
                                          "etdInfo": {"dropoffETARange": {"min": 10, "max": 20}}}}}


def store_data(uuid: str, title: str, price: int = 800) -> dict:
    return {"uuid": uuid, "title": title, "slug": title.lower(), "isOpen": True, "isOrderable": True,
            "etaRange": {"text": "Entre 15 et 30 min"},
            "storeInfoMetadata": {"rawRatingStats": {"storeRatingScore": 4.71, "ratingCount": "1,500+"}},
            "catalogSectionsMap": {"s": [{"payload": {"standardItemsPayload": {
                "title": {"text": "Burgers"},
                "catalogItems": [{"uuid": f"{uuid}-1", "title": "Cheese burger", "price": price}]}}}]}}


U = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(6)]


class FakeSession:
    """Remplace UberEatsSession : flux de 4 restaurants, dont U[3] mal noté ; U[5] hors du flux."""
    calls: list[str] = []

    def __init__(self, cfg):
        pass

    def __enter__(self):
        FakeSession.calls = []
        return self

    def __exit__(self, *exc):
        pass

    def fetch_feed(self):
        return [feed_entry(U[0], "A", promo=True), feed_entry(U[1], "B"), feed_entry(U[2], "C"),
                feed_entry(U[3], "D", rating=3.2)]

    def fetch_store(self, uuid):
        FakeSession.calls.append(uuid)
        return store_data(uuid, {U[5]: "Loin"}.get(uuid, uuid[-1]))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "UberEatsSession", FakeSession)
    c = copy.deepcopy(load_config())
    c["storage"]["data_dir"] = str(tmp_path)
    c["fetch"]["max_stores"] = 2
    c["prefilter"]["min_rating"] = 4.0  # D (note 3,2) ne passe le pré-filtre que comme favori
    c["deliveroo"]["enabled"] = False
    c["favorites"] = [f"https://www.ubereats.com/fr/store/d/{_b64(U[3])}", U[5], U[3], "pas un lien"]
    return c


def _b64(uuid: str) -> str:
    import base64
    import uuid as m
    return base64.urlsafe_b64encode(m.UUID(uuid).bytes).decode().rstrip("=")


def test_favorite_uuids_dedupes_and_skips_invalid(cfg):
    assert engine.favorite_uuids(cfg) == [U[3], U[5]]


def test_favorites_are_always_fetched(cfg):
    raw = engine.fetch_raw(cfg)
    # 2 retenus par le pré-filtre (A en premier grâce à sa promo), plus les 2 favoris :
    # D (note trop basse pour le pré-filtre) et U[5] (absent du flux).
    assert FakeSession.calls == [U[0], U[1], U[3], U[5]]
    assert raw["favorites"] == [U[3], U[5]]


def test_favorite_absent_from_feed_is_rebuilt_from_its_menu(cfg):
    stores = {s.uuid: s for s in engine.build_stores(engine.fetch_raw(cfg))}
    far = stores[U[5]]
    assert far.title == "Loin" and far.url == store_url("loin", U[5])
    assert (far.eta_min, far.eta_max, far.rating, far.rating_count) == (15, 30, 4.71, 1500)
    assert [it.title for it in far.items] == ["Cheese burger"]


def test_cache_is_stale_when_favorites_change(cfg):
    engine.fetch_raw(cfg)
    assert engine.get_raw(cfg)["favorites"] == [U[3], U[5]] and FakeSession.calls  # même favoris : cache
    FakeSession.calls = []
    engine.get_raw(cfg)
    assert FakeSession.calls == []
    cfg["favorites"] = [U[2]]
    engine.get_raw(cfg)
    assert U[2] in FakeSession.calls


def test_export_has_catalog_and_favorites(cfg):
    raw = engine.fetch_raw(cfg)
    data = export_stores(engine.build_stores(raw), cfg, datetime(2026, 10, 27, 12, tzinfo=PARIS),
                         engine.build_catalog(raw), raw["favorites"])
    with_menu = {s["uuid"] for s in data["stores"]}
    assert with_menu == {U[0], U[1], U[3], U[5]}
    assert [c["uuid"] for c in data["catalog"]] == [U[2]]          # seulement ceux sans menu
    assert set(data["catalog"][0]) == {"uuid", "title", "url", "rating", "rating_count", "eta_min", "eta_max", "lat", "lng"}
    assert data["favorites"] == [U[3], U[5]]
    json.dumps(data)  # sérialisable tel quel


def test_old_cache_without_favorites_still_loads(cfg):
    raw = engine.fetch_raw(cfg)
    raw.pop("favorites")
    path = Path(cfg["storage"]["data_dir"]) / "raw" / "latest.json.gz"
    path.write_bytes(gzip.compress(json.dumps(raw).encode()))
    cfg["favorites"] = []
    assert engine.get_raw(cfg).get("favorites") is None   # pas de favoris de part et d'autre : cache gardé


def test_parse_store_only_without_eta_or_slug():
    s = parse_store_only({"uuid": U[1], "title": "X"})
    assert (s.eta_min, s.url.endswith("ubereats.com")) == (None, True)
