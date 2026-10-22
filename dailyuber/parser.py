"""Conversion des réponses JSON brutes d'Uber Eats en objets du modèle."""
import re

from .fetcher import BASE_URL
from .models import Item, Store, ThresholdPromo

_NUM = r"(\d+(?:[.,]\d+)?)"
_PCT_RE = re.compile(rf"-\s*{_NUM}\s*%\s*dès\s*{_NUM}\s*€", re.I)
_AMOUNT_RE = re.compile(rf"(?:-\s*{_NUM}\s*€|{_NUM}\s*€\s*(?:de réduction|offerts?))\s*dès\s*{_NUM}\s*€", re.I)
_GIFT_RE = re.compile(rf"article offert dès\s*{_NUM}\s*€", re.I)
_FARE_RE = re.compile(rf"frais de livraison\s*(?::|à)\s*{_NUM}\s*€", re.I)
_DISCOUNTED_FROM_RE = re.compile(rf"discounted from\s*{_NUM}", re.I)
_FREE_DELIVERY_RE = re.compile(r"livraison offerte|frais de livraison\s*(?:à|:)\s*0(?:[.,]00)?\s*€", re.I)
_CONDITION_RE = re.compile(rf"(?:supérieures? à|dès|à partir de)\s*{_NUM}\s*€", re.I)


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_threshold_promos(texts: list[str]) -> list[ThresholdPromo]:
    promos = []
    for t in texts:
        t = t.strip()
        if m := _PCT_RE.search(t):
            promos.append(ThresholdPromo(min_subtotal=_num(m[2]), pct_off=_num(m[1]) / 100, label=t))
        elif m := _AMOUNT_RE.search(t):
            amount = m[1] or m[2]
            promos.append(ThresholdPromo(min_subtotal=_num(m[3]), amount_off=_num(amount), label=t))
        elif m := _GIFT_RE.search(t):
            # Article offert : on ne sait pas lequel, aucune remise chiffrée, juste signalé.
            promos.append(ThresholdPromo(min_subtotal=_num(m[1]), label=t))
    return promos


def parse_delivery_fee(texts: list[str]) -> tuple[float | None, float | None]:
    """(frais de livraison affichés, seuil de livraison offerte), hors offre "nouveaux utilisateurs"."""
    fee, free_min = None, None
    for t in texts:
        if "nouveaux utilisateurs" in t.lower():
            continue
        if _FREE_DELIVERY_RE.search(t):
            if m := _CONDITION_RE.search(t):
                free_min = _num(m[1])
            else:
                fee = 0.0
        elif m := _FARE_RE.search(t):
            fee = _num(m[1])
    return fee, free_min


def parse_feed_store(raw: dict) -> Store:
    payload = (raw.get("tracking") or {}).get("storePayload") or {}
    rating_info = payload.get("ratingInfo") or {}
    eta = (payload.get("etdInfo") or {}).get("dropoffETARange") or {}
    meta_texts = [m.get("text", "") for m in raw.get("meta") or []]
    signposts = [s.get("text", "").strip() for s in raw.get("signposts") or []]
    action = (raw.get("actionUrl") or "").split("?")[0]
    delivery_fee, free_delivery_min = parse_delivery_fee(meta_texts + signposts)
    return Store(
        uuid=raw["storeUuid"],
        title=raw["title"]["text"],
        url=f"{BASE_URL}/fr{action}" if action else BASE_URL,
        rating=round(rating_info["storeRatingScore"], 2) if rating_info.get("storeRatingScore") else None,
        rating_count=int(re.sub(r"\D", "", str(rating_info.get("ratingCount") or "")) or 0),  # "3,000+"
        eta_min=eta.get("min"),
        eta_max=eta.get("max"),
        delivery_fee=delivery_fee,
        free_delivery_min=free_delivery_min,
        signposts=signposts,
        threshold_promos=parse_threshold_promos(signposts),
    )


def _bogo(item: dict) -> tuple[int, int, int]:
    promo = (item.get("itemPromotion") or {}).get("buyXGetYItemPromotion")
    if not promo:
        return 0, 0, 0
    return int(promo.get("buyQuantity") or 0), int(promo.get("getQuantity") or 0), int(promo.get("maxRedemptionCount") or 0)


def parse_store_details(store: Store, raw: dict) -> Store:
    """Complète un Store issu du flux avec le menu et l'état d'ouverture (getStoreV1)."""
    store.is_open = bool(raw.get("isOpen")) and bool(raw.get("isOrderable", True))
    if store.rating is None:
        stats = (raw.get("storeInfoMetadata") or {}).get("rawRatingStats") or {}
        if stats.get("storeRatingScore"):
            store.rating = round(stats["storeRatingScore"], 2)

    by_uuid: dict[str, Item] = {}
    for sections in (raw.get("catalogSectionsMap") or {}).values():
        for sec in sections:
            p = (sec.get("payload") or {}).get("standardItemsPayload") or {}
            sec_title = (p.get("title") or {}).get("text", "")
            for it in p.get("catalogItems") or []:
                if it.get("isSoldOut") or it.get("isAvailable") is False or not it.get("price"):
                    continue
                uuid = it.get("uuid") or it["title"]
                if uuid in by_uuid:
                    if sec_title and sec_title not in by_uuid[uuid].section:
                        by_uuid[uuid].section += f" / {sec_title}"
                    continue
                access = (it.get("priceTagline") or {}).get("accessibilityText", "")
                m = _DISCOUNTED_FROM_RE.search(access)
                buy, get, max_ = _bogo(it)
                by_uuid[uuid] = Item(
                    title=it["title"].strip(),
                    price=it["price"] / 100,
                    section=sec_title,
                    original_price=_num(m[1]) if m else None,
                    bogo_buy=buy, bogo_get=get, bogo_max=max_,
                    has_customizations=bool(it.get("hasCustomizations")),
                )
    store.items = list(by_uuid.values())
    return store
