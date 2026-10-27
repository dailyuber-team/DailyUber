"""Récupération des données Uber Eats.

Uber Eats bloque les appels directs (403) aux endpoints JSON de la page d'accueil
sans session navigateur. On ouvre donc une vraie page avec Playwright, puis on
appelle les endpoints internes depuis cette page (mêmes cookies, mêmes en-têtes).
"""
import base64
import json
import logging
import random
import time
import urllib.parse

from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

BASE_URL = "https://www.ubereats.com"
MAX_FEED_PAGES = 30  # garde-fou pour max_feed_pages: 0 (le flux d'Aix : environ 400 restaurants, 5 pages)

FEED_BODY = {
    "cacheKey": "", "feedSessionCount": {"announcementCount": 0, "announcementLabel": ""},
    "userQuery": "", "date": "", "startTime": 0, "endTime": 0, "carouselId": "",
    "sortAndFilters": [], "billboardUuid": "", "feedProvider": "", "promotionUuid": "",
    "targetingStoreTag": "", "venueUUID": "", "selectedSectionUUID": "", "favorites": "",
    "vertical": "", "searchSource": "", "searchType": "", "keyName": "",
    "serializedRequestContext": "", "isUserInitiatedRefresh": False,
}

_API_JS = """async ([ep, body]) => {
  const r = await fetch(`/_p/api/${ep}?localeCode=fr`, {
    method: "POST",
    headers: {"content-type": "application/json", "x-csrf-token": "x"},
    body: JSON.stringify(body),
  });
  return {status: r.status, text: await r.text()};
}"""


class FetchError(RuntimeError):
    pass


def encode_location(address: dict) -> str:
    """Encode l'adresse au format du paramètre `pl` de /feed (base64 d'un JSON url-encodé)."""
    loc = {
        "address": address["label"],
        "latitude": address["latitude"],
        "longitude": address["longitude"],
        "reference": address.get("reference", ""),
        "referenceType": "google_places",
    }
    return base64.b64encode(urllib.parse.quote(json.dumps(loc, separators=(",", ":"))).encode()).decode()


class UberEatsSession:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._pw = self._browser = self.page = None

    def __enter__(self):
        f = self.cfg["fetch"]
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=f["headless"])
        context = self._browser.new_context(
            user_agent=f["user_agent"], locale="fr-FR", timezone_id="Europe/Paris",
            viewport={"width": 1366, "height": 900},
        )
        self.page = context.new_page()
        url = f"{BASE_URL}/fr/feed?diningMode=DELIVERY&pl={urllib.parse.quote(encode_location(self.cfg['address']))}"
        log.info("Ouverture de %s", url[:80])
        resp = self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        if resp is None or resp.status >= 400:
            raise FetchError(f"Page feed inaccessible (HTTP {resp.status if resp else '?'})")
        self.page.wait_for_timeout(3000)
        return self

    def __exit__(self, *exc):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def _pause(self):
        f = self.cfg["fetch"]
        time.sleep(random.uniform(f["min_delay_s"], f["max_delay_s"]))

    def api(self, endpoint: str, body: dict) -> dict:
        res = self.page.evaluate(_API_JS, [endpoint, body])
        if res["status"] != 200:
            raise FetchError(f"{endpoint}: HTTP {res['status']} {res['text'][:200]}")
        data = json.loads(res["text"])
        if data.get("status") != "success":
            raise FetchError(f"{endpoint}: réponse {data.get('status')} {str(data.get('data'))[:200]}")
        return data["data"]

    def fetch_feed(self) -> list[dict]:
        """Retourne les entrées REGULAR_STORE brutes du flux, toutes pages confondues."""
        stores, seen = [], set()
        # 0 = jusqu'à la dernière page. Uber place les restaurants pas encore ouverts en fin de
        # flux : lire seulement les premières pages les fait disparaître le matin.
        body, pages = dict(FEED_BODY), self.cfg["fetch"]["max_feed_pages"] or MAX_FEED_PAGES
        for page_no in range(pages):
            data = self.api("getFeedV1", body)
            for item in data.get("feedItems", []):
                s = item.get("store") if item.get("type") == "REGULAR_STORE" else None
                if s and s["storeUuid"] not in seen:
                    seen.add(s["storeUuid"])
                    stores.append(s)
            meta = data.get("meta") or {}
            log.info("Flux page %d : %d restaurants cumulés", page_no + 1, len(stores))
            if not meta.get("hasMore"):
                break
            body = {**FEED_BODY, "pageInfo": {"offset": meta["offset"], "pageSize": 80}}
            self._pause()
        if not stores:
            raise FetchError("Flux vide : aucun restaurant renvoyé pour cette adresse")
        return stores

    def fetch_store(self, store_uuid: str) -> dict:
        self._pause()
        return self.api("getStoreV1", {"storeUuid": store_uuid})
