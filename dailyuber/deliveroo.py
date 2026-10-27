"""Deliveroo : restaurants, menus, promos et créneaux, depuis les pages HTML du site.

Pas d'API publique. Les pages (Next.js) contiennent leurs données dans
<script id="__NEXT_DATA__"> : une simple requête HTTP suffit, sans navigateur.
- liste d'un quartier : props.initialState.home.feed.results (blocs "partner-card"),
  avec `?day=today|tomorrow&time=HH:MM` pour ne garder que ceux qui livrent à cette heure ;
- menu d'un restaurant : props.initialState.menuPage.menu (metas.root : items, categories, offres ;
  header : "Ferme à 22:30", "Montant min. de 10,00 €", "1,79 € de livraison").
Deliveroo n'affiche pas d'horaires hebdomadaires : on interroge la liste à quelques heures
(`deliveroo.sample_times`) et chaque présence devient un créneau d'une heure.
robots.txt n'interdit pas ces pages (seulement /api/, */graphql…) ; les CGU interdisent en
revanche l'extraction automatisée, comme celles d'Uber Eats (voir CONTEXT.md).
"""
import gzip
import json
import logging
import random
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .hours import PARIS
from .models import Item, Store, ThresholdPromo

log = logging.getLogger(__name__)

BASE_URL = "https://deliveroo.fr"
GEOCODER = "https://api-adresse.data.gouv.fr/search/"  # géocodeur public de l'État, sans clé
BOGO_MAX = 1  # "1 plat acheté = 1 plat offert" : nombre d'applications inconnu, on reste prudent
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_NUM = r"(\d+(?:[.,]\d+)?)"


class DeliverooError(RuntimeError):
    pass


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def geohash(lat: float, lng: float, precision: int = 12) -> str:
    """Geohash standard (Deliveroo situe l'adresse de livraison avec)."""
    chars, bits, ch, even = "0123456789bcdefghjkmnpqrstuvwxyz", [16, 8, 4, 2, 1], 0, True
    lat_r, lng_r, out, bit = [-90.0, 90.0], [-180.0, 180.0], "", 0
    while len(out) < precision:
        rng, val = (lng_r, lng) if even else (lat_r, lat)
        mid = (rng[0] + rng[1]) / 2
        if val >= mid:
            ch |= bits[bit]
            rng[0] = mid
        else:
            rng[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out += chars[ch]
            bit, ch = 0, 0
    return out


# --- Lecture des pages ----------------------------------------------------------------------

def next_data(html: str) -> dict:
    m = _NEXT_DATA.search(html)
    if not m:
        raise DeliverooError("page sans __NEXT_DATA__ (protection anti-robot ?)")
    return json.loads(m[1])


def _walk(x, visit):
    if isinstance(x, dict):
        visit(x)
        for v in x.values():
            _walk(v, visit)
    elif isinstance(x, list):
        for v in x:
            _walk(v, visit)


def parse_listing(data: dict) -> dict[str, dict]:
    """Restaurants d'une page de liste : id -> {name, href, rating, rating_count, eta, promos}."""
    results = data["props"]["initialState"]["home"]["feed"]["results"]
    out: dict[str, dict] = {}

    def visit(block):
        if block.get("typeName") != "UIRooBlock" or "partner-card" not in (block.get("rooTemplateId") or ""):
            return
        params = []
        _walk(block, lambda x: params.append(x["parameters"]) if x.get("name") == "navigate_to_restaurant" else None)
        if not params or params[0].get("restaurant_id") in out:
            return
        p, d = params[0], block.get("data") or {}
        eta = [int(x) for x in re.findall(r"\d+", d.get("home-units-delivery-time.content")
                                         or d.get("estimated-delivery-bubble-type-default-partner-delivery-time.content")
                                         or d.get("partner-delivery-time.content") or "")]
        rating = d.get("partner-rating.content")
        out[p["restaurant_id"]] = {
            "name": p.get("restaurant_name") or d.get("partner-name.content") or "?",
            "href": (p.get("restaurant_href") or "").split("?")[0],
            "rating": float(rating) if rating and re.fullmatch(r"\d+(?:\.\d+)?", rating) else None,
            "rating_count": int(re.sub(r"\D", "", d.get("partner-rating-count.content") or "") or 0),
            "eta": [eta[0], eta[-1]] if eta else None,
            "promos": sorted({v.strip() for k, v in d.items()
                              if k.startswith("promotional") and k.endswith("label.content") and v}),
        }

    _walk(results, visit)
    return out


def _texts(x) -> list[str]:
    out = []
    _walk(x, lambda d: out.append(d["text"]) if isinstance(d.get("text"), str) else None)
    return out


def trim_menu(data: dict) -> dict:
    """Ne garde du menu que ce qui sert (une page fait ~2,7 Mo)."""
    menu = data["props"]["initialState"]["menuPage"]["menu"]
    root = menu["metas"]["root"]
    offer = root.get("offer") or {}
    keep = ("id", "name", "categoryId", "price", "priceDiscounted", "offerText", "available", "modifierGroupIds")
    return {
        "restaurant": {k: (root.get("restaurant") or {}).get(k) for k in ("id", "name", "uname", "location", "links")},
        "categories": root.get("categories") or [],
        "items": [{k: it.get(k) for k in keep} for it in root.get("items") or []],
        "offers": offer.get("offers") or [],
        "progress_offer": (offer.get("progressBar") or {}).get("offer"),
        "header": [t for t in _texts(menu.get("header")) if t.strip() and t != "·"],
    }


def parse_promos(texts: list[str]) -> tuple[list[ThresholdPromo], float | None]:
    """Badges de la liste : "Commandez pour 15 €, profitez de -15 %", "… Économisez la livraison",
    "Un produit offert dès 30 € d'achat". Renvoie (promos à seuil, seuil de livraison offerte)."""
    promos, free_min = [], None
    for t in texts:
        t = t.replace("‪", "").replace("‬", "")
        if m := re.search(rf"commandez pour\s*{_NUM}\s*€.*?-\s*{_NUM}\s*%", t, re.I):
            promos.append(ThresholdPromo(_num(m[1]), pct_off=_num(m[2]) / 100, label=t))
        elif m := re.search(rf"commandez pour\s*{_NUM}\s*€.*?-\s*{_NUM}\s*€", t, re.I):
            promos.append(ThresholdPromo(_num(m[1]), amount_off=_num(m[2]), label=t))
        elif m := re.search(rf"commandez pour\s*{_NUM}\s*€.*livraison", t, re.I):
            free_min = _num(m[1]) if free_min is None else min(free_min, _num(m[1]))
        elif m := re.search(rf"offert dès\s*{_NUM}\s*€", t, re.I):
            promos.append(ThresholdPromo(_num(m[1]), label=t))
    return promos, free_min


def menu_promos(offers: list[dict]) -> list[ThresholdPromo]:
    """Offres du menu qui dépendent du montant de la commande (le badge de la liste est souvent un
    gabarit non rempli : "-{Amount} dès {MOV} d'achat").
    - FullMenuPercentOffOffer : -X % sur le sous-total dès Y € (Y peut valoir 0). Deliveroo affiche
      déjà ce prix remisé sur chaque plat, même quand il faut atteindre Y € : build_store remet donc
      ces plats au prix plein et la remise ne compte qu'une fois, au sous-total. Avec un plafond,
      l'offre est ignorée (plats au prix plein, sans remise) : on ne sait pas la modéliser ;
    - FreeItemOffer : article offert dès Y €, signalé sans valeur.
    Les remises sur un plat précis (ItemSpecificPercentOffOffer…) sont lues dans priceDiscounted."""
    out = []
    for o in offers:
        mov = ((o.get("minimumOrderValue") or {}).get("fractional") or 0) / 100
        mov_txt = f"{mov:g}".replace(".", ",")
        if o.get("typeName") == "FullMenuPercentOffOffer" and o.get("percentageDiscount") and not o.get("maxDiscount"):
            pct = o["percentageDiscount"]
            label = f"-{pct} % dès {mov_txt} €" if mov else f"-{pct} % sur tout le menu"
            out.append(ThresholdPromo(mov, pct_off=pct / 100, label=label))
        elif o.get("typeName") == "FreeItemOffer" and mov:
            out.append(ThresholdPromo(mov, label=f"Article offert dès {mov_txt} €"))
    return out


def _slots(presence: dict[str, list[str]], uid: str) -> dict[str, list[tuple[int, int]]]:
    """{"2026-10-27 12:00": [ids]} -> créneaux d'une heure centrés sur chaque heure où il livre."""
    slots: dict[str, list[tuple[int, int]]] = {}
    for key, ids in presence.items():
        if uid in ids:
            day, hm = key.split(" ")
            h, m = (int(x) for x in hm.split(":"))
            slots.setdefault(day, []).append((h * 60 + m - 30, h * 60 + m + 30))
    return {d: sorted(s) for d, s in slots.items()}


def build_store(rid: str, listing: dict, menu: dict, presence: dict[str, list[str]], now_ids: dict,
                coords: list | None) -> Store:
    header = menu.get("header") or []
    joined = " | ".join(header)
    promos, free_min = parse_promos(listing.get("promos") or [])
    progress = menu.get("progress_offer") or {}
    if progress.get("typeName") == "FreeDeliveryOffer" and progress.get("minimumOrderValue"):
        value = progress["minimumOrderValue"]["fractional"] / 100
        free_min = value if free_min is None else min(free_min, value)
    fee = re.search(rf"{_NUM}\s*€ de livraison", joined)
    min_order = re.search(rf"Montant min\.? de\s*{_NUM}\s*€", joined)
    closes = re.search(r"Ferme à\s*(\d{1,2}:\d{2})", joined)
    opens = re.search(r"Ouvre (?:à|le)[^|]*", joined)
    now = now_ids.get(rid)
    is_open = bool(now and now.get("eta"))
    distance = next((i for i, t in enumerate(header) if re.match(r"À \d", t)), None)

    unique = {}  # le badge de la liste et l'offre du menu décrivent souvent la même promo
    for p in promos + menu_promos(menu.get("offers") or []):
        unique.setdefault((p.min_subtotal, p.pct_off, p.amount_off), p)
    promos = list(unique.values())
    bogo_ids = {i for o in menu.get("offers") or [] if o.get("typeName") == "BuyOneGetOneFreeOffer"
                for i in o.get("itemIds") or []}
    categories = {c["id"]: c.get("name") or "" for c in menu.get("categories") or []}
    full_menu_pcts = [o["percentageDiscount"] / 100 for o in menu.get("offers") or []
                      if o.get("typeName") == "FullMenuPercentOffOffer" and o.get("percentageDiscount")]
    items = []
    for it in menu.get("items") or []:
        if not it.get("available") or not (it.get("price") or {}).get("fractional"):
            continue
        price = it["price"]["fractional"] / 100
        disc = (it.get("priceDiscounted") or {}).get("fractional")
        original = None
        if disc and any(abs(1 - disc / 100 / price - p) <= 0.011 for p in full_menu_pcts):
            disc = None  # remise "tout le menu" affichée sur le plat : comptée au sous-total
        if disc and 0 < disc / 100 < price:  # remise sur le plat ; 0 = l'article offert d'un 1+1
            original, price = price, disc / 100
        bogo = it["id"] in bogo_ids
        items.append(Item(title=it["name"].strip(), price=price, section=categories.get(it.get("categoryId"), ""),
                          original_price=original, bogo_buy=int(bogo), bogo_get=int(bogo), bogo_max=BOGO_MAX if bogo else 0,
                          has_customizations=bool(it.get("modifierGroupIds"))))
    eta = (now or listing).get("eta")
    return Store(
        uuid=f"deliveroo:{rid}", title=listing["name"], url=BASE_URL + "/fr" + urllib.parse.quote(listing["href"]),
        rating=listing.get("rating"), rating_count=listing.get("rating_count") or 0,
        eta_min=eta[0] if eta else None, eta_max=eta[1] if eta else None,
        delivery_fee=_num(fee[1]) if fee else None, free_delivery_min=free_min,
        signposts=listing.get("promos") or [], threshold_promos=promos,
        is_open=is_open, open_until=f"Ouvert jusqu'à {closes[1]}" if closes else (opens[0].strip() if opens else ""),
        hours=None, delivery_slots=_slots(presence, rid),
        cuisines=header[:distance] if distance else [],
        platform="deliveroo", min_order=_num(min_order[1]) if min_order else None,
        lat=coords[0] if coords else None, lng=coords[1] if coords else None,
        items=items,
    )


def build_stores(raw: dict | None) -> list[Store]:
    if not raw:
        return []
    now_ids = raw["listings"].get("now") or {}
    every = {**{k: v for lst in raw["listings"].values() for k, v in lst.items()}, **now_ids}
    presence = {k: list(v) for k, v in raw["listings"].items() if k != "now"}
    geo = raw.get("geocode") or {}
    stores = []
    for rid, menu in raw["menus"].items():
        address = ((menu.get("restaurant") or {}).get("location") or {}).get("address") or {}
        stores.append(build_store(rid, every.get(rid) or {"name": "?", "href": ""}, menu, presence, now_ids,
                                  geo.get(address.get("address1") or "")))
    return stores


# --- Récupération ---------------------------------------------------------------------------

class DeliverooClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.first = True

    def _pause(self):
        if not self.first:
            f = self.cfg["fetch"]
            time.sleep(random.uniform(f["min_delay_s"], f["max_delay_s"]))
        self.first = False

    def get(self, url: str, headers: dict | None = None) -> bytes:
        self._pause()
        req = urllib.request.Request(url, headers={
            "User-Agent": self.cfg["fetch"]["user_agent"], "Accept-Language": "fr-FR,fr;q=0.9",
            "Accept-Encoding": "gzip", **(headers or {})})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            return gzip.decompress(body) if r.headers.get("Content-Encoding") == "gzip" else body

    def page(self, path: str, query: dict) -> dict:
        url = f"{BASE_URL}/fr{urllib.parse.quote(path)}?{urllib.parse.urlencode(query)}"
        return next_data(self.get(url).decode("utf-8", errors="replace"))


def sample_keys(cfg: dict, now: datetime) -> list[tuple[str, str, str]]:
    """(clé "AAAA-MM-JJ HH:MM", day, time) des heures à interroger, aujourd'hui et demain,
    en sautant celles déjà passées."""
    out = []
    for offset, day in ((0, "today"), (1, "tomorrow")):
        date = (now + timedelta(days=offset)).date()
        for hm in cfg["deliveroo"]["sample_times"]:
            h, m = (int(x) for x in hm.split(":"))
            when = datetime(date.year, date.month, date.day, h, m, tzinfo=PARIS)
            if when > now + timedelta(minutes=30):
                out.append((f"{date.isoformat()} {hm}", day, hm))
    return out


def fetch_raw(cfg: dict, progress=None) -> dict:
    """Listes (maintenant + heures échantillons), puis un menu par restaurant et le géocodage."""
    dcfg, addr = cfg["deliveroo"], cfg["address"]
    client = DeliverooClient(cfg)
    gh = geohash(addr["latitude"], addr["longitude"])
    path = f"/restaurants/{dcfg['city']}/{dcfg['neighborhood']}"
    now = datetime.now(PARIS)
    raw = {"fetched_at": now.isoformat(timespec="seconds"), "listings": {}, "menus": {}, "geocode": {}}

    raw["listings"]["now"] = parse_listing(client.page(path, {"geohash": gh}))
    if not raw["listings"]["now"]:
        raise DeliverooError("liste vide : aucun restaurant Deliveroo")
    when_for = {}  # restaurant -> (day, time) pour lire son menu à une heure où il livre
    for key, day, hm in sample_keys(cfg, now):
        found = parse_listing(client.page(path, {"geohash": gh, "day": day, "time": hm}))
        raw["listings"][key] = found
        for rid in found:
            when_for.setdefault(rid, (day, hm))
        log.info("Deliveroo %s : %d restaurants", key, len(found))
    every = {k: v for lst in raw["listings"].values() for k, v in lst.items()}
    log.info("Deliveroo : %d restaurants au total", len(every))

    for i, (rid, info) in enumerate(every.items(), 1):
        day, hm = when_for.get(rid, ("today", "ASAP"))
        try:
            raw["menus"][rid] = trim_menu(client.page(info["href"], {"day": day, "geohash": gh, "time": hm}))
            log.info("[Deliveroo %d/%d] %s", i, len(every), info["name"])
        except Exception as e:  # un menu en échec ne doit pas faire tomber la recherche
            log.warning("[Deliveroo %d/%d] %s : échec (%s)", i, len(every), info["name"], e)
        if progress:
            progress(i, len(every), info["name"])

    raw["geocode"] = geocode_menus(raw["menus"], cfg)
    return raw


def geocode_menus(menus: dict, cfg: dict) -> dict[str, list[float]]:
    """Adresse -> [lat, lng] via api-adresse.data.gouv.fr (Deliveroo ne donne que l'adresse)."""
    out = {}
    for menu in menus.values():
        address = ((menu.get("restaurant") or {}).get("location") or {}).get("address") or {}
        a1 = address.get("address1")
        if not a1 or a1 in out:
            continue
        q = urllib.parse.urlencode({"q": re.sub(r",\s*,", ",", a1), "limit": 1,
                                    "lat": cfg["address"]["latitude"], "lon": cfg["address"]["longitude"]})
        try:
            time.sleep(0.2)  # le géocodeur accepte 50 requêtes/s ; inutile d'appeler Deliveroo pour ça
            req = urllib.request.Request(f"{GEOCODER}?{q}", headers={"User-Agent": "DailyUber (outil perso)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                feats = json.load(r).get("features") or []
            if feats and feats[0]["properties"].get("score", 0) >= 0.5:
                lng, lat = feats[0]["geometry"]["coordinates"]
                out[a1] = [lat, lng]
        except Exception as e:
            log.warning("Géocodage impossible pour %r : %s", a1, e)
    return out
