"""Flux lu jusqu'à la dernière page, et coordonnées des restaurants pour le rayon "Autour de moi"."""
import copy
from datetime import datetime

from dailyuber import fetcher
from dailyuber.config import load_config
from dailyuber.export import export_stores
from dailyuber.hours import PARIS
from dailyuber.parser import parse_feed_store, parse_store_details


def _session(max_pages: int, pages: list[list[str]]):
    """UberEatsSession sans navigateur : getFeedV1 renvoie `pages` (des UUID), une page par appel."""
    s = object.__new__(fetcher.UberEatsSession)
    s.cfg = {"fetch": {"max_feed_pages": max_pages}}
    s.calls = 0
    s._pause = lambda: None

    def api(endpoint, body):
        i = s.calls
        s.calls += 1
        assert ("pageInfo" in body) == (i > 0)
        items = [{"type": "REGULAR_STORE", "store": {"storeUuid": u}} for u in pages[i]]
        return {"feedItems": items, "meta": {"hasMore": i + 1 < len(pages), "offset": 80 * (i + 1)}}

    s.api = api
    return s


PAGES = [["a", "b"], ["c"], ["d", "a"], ["e"], ["f"]]


def test_feed_reads_every_page_when_max_is_zero():
    s = _session(0, PAGES)
    assert [x["storeUuid"] for x in s.fetch_feed()] == ["a", "b", "c", "d", "e", "f"]  # "a" en double ignoré
    assert s.calls == 5


def test_feed_stops_at_max_pages():
    s = _session(3, PAGES)
    assert [x["storeUuid"] for x in s.fetch_feed()] == ["a", "b", "c", "d"]
    assert s.calls == 3


def test_feed_has_a_safety_cap():
    s = _session(0, [[f"u{i}"] for i in range(fetcher.MAX_FEED_PAGES + 10)])
    s.fetch_feed()
    assert s.calls == fetcher.MAX_FEED_PAGES


def test_coordinates_from_feed_then_store_location():
    feed = {"storeUuid": "u", "title": {"text": "Resto"}, "mapMarker": {"latitude": 43.52, "longitude": 5.44}}
    s = parse_feed_store(feed)
    assert (s.lat, s.lng) == (43.52, 5.44)
    parse_store_details(s, {"isOpen": True, "location": {"latitude": 43.5169, "longitude": 5.4496}})
    assert (s.lat, s.lng) == (43.5169, 5.4496)      # l'adresse du restaurant est plus précise
    parse_store_details(s, {"isOpen": True, "location": {}})
    assert (s.lat, s.lng) == (43.5169, 5.4496)      # pas d'adresse : on garde ce qu'on a


def test_export_has_center_and_coordinates():
    cfg = copy.deepcopy(load_config())
    feed = {"storeUuid": "u", "title": {"text": "Resto"}, "mapMarker": {"latitude": 43.52, "longitude": 5.44}}
    s = parse_feed_store(feed)
    data = export_stores([], cfg, datetime(2026, 10, 27, 12, tzinfo=PARIS), [s], [])
    assert data["center"] == {"lat": cfg["address"]["latitude"], "lng": cfg["address"]["longitude"]}
    assert (data["catalog"][0]["lat"], data["catalog"][0]["lng"]) == (43.52, 5.44)
