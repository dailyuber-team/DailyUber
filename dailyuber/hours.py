"""Horaires d'ouverture et créneaux de livraison planifiables.

Sources (getStoreV1) :
- `hours` : [{"dayRange": "Lundi - Samedi", "sectionHours": [{"startTime": 690, "endTime": 870}]}],
  minutes depuis minuit, heure de Paris ;
- `adaptedDeliveryHoursInfos` : créneaux de livraison planifiables par date
  ({"dates": [...], "timeRanges": {"2026-10-23": [{"startTime": 690, "endTime": 720}, ...]}}).
"""
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PARIS = ZoneInfo("Europe/Paris")
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
NOW_MARGIN_MIN = 30  # une heure visée à moins de 30 min est traitée comme "maintenant"

# jour de la semaine (0 = lundi) -> plages [début, fin) en minutes, fin <= 1440
WeeklyHours = dict[int, list[tuple[int, int]]]


def parse_day_range(text: str) -> set[int] | None:
    """"Lundi - Vendredi" -> {0..4} ; "Dimanche - Jeudi" -> {6,0,1,2,3} ; None si illisible."""
    t = text.strip().lower()
    if t in ("tous les jours", "chaque jour"):
        return set(range(7))
    days: set[int] = set()
    for part in re.split(r"\s*(?:,|\bet\b)\s*", t):
        bounds = [b.strip() for b in part.split("-")]
        if not all(b in DAYS for b in bounds) or len(bounds) not in (1, 2):
            return None
        a, b = DAYS.index(bounds[0]), DAYS.index(bounds[-1])
        days |= {(a + i) % 7 for i in range((b - a) % 7 + 1)}
    return days


def parse_hours(raw_hours: list[dict] | None) -> WeeklyHours | None:
    """Horaires hebdomadaires. Une plage qui passe minuit est coupée sur deux jours."""
    if not raw_hours:
        return None
    weekly: WeeklyHours = {d: [] for d in range(7)}
    for entry in raw_hours:
        days = parse_day_range(entry.get("dayRange", ""))
        if days is None:
            log.warning("Plage de jours non reconnue : %r", entry.get("dayRange"))
            return None
        for sec in entry.get("sectionHours") or []:
            start, end = sec["startTime"], sec["endTime"]
            if end <= start:  # ex. 18:00 -> 02:00
                end += 1440
            for d in days:
                weekly[d].append((start, min(end, 1440)))
                if end > 1440:
                    weekly[(d + 1) % 7].append((0, end - 1440))
    return weekly


def is_open_at(weekly: WeeklyHours, when: datetime) -> bool:
    minute = when.hour * 60 + when.minute
    return any(start <= minute < end for start, end in weekly.get(when.weekday(), []))


def parse_delivery_slots(raw: dict | None) -> dict[str, list[tuple[int, int]]]:
    """Créneaux planifiables : "2026-10-23" -> [(690, 720), ...]."""
    ranges = (raw or {}).get("timeRanges") or {}
    return {d: [(r["startTime"], r["endTime"]) for r in slots] for d, slots in ranges.items()}


def hhmm(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def availability(store, when: datetime | None, now: datetime) -> str | None:
    """Libellé de disponibilité du restaurant pour l'heure visée, ou None s'il n'est pas commandable.

    - `when` absent (ou dans moins de 30 min) : il faut que le restaurant accepte les commandes
      (`isOpen`/`isOrderable` au moment de la récupération) ET soit encore ouvert d'après ses horaires.
    - `when` plus tard : on se fie aux créneaux de livraison planifiables d'Uber quand la date
      est dans leur horizon (environ 5 jours), sinon aux horaires d'ouverture.
    """
    if when is None or when - now <= timedelta(minutes=NOW_MARGIN_MIN):
        if not store.is_open:
            return None
        if store.hours is not None and not is_open_at(store.hours, now):
            return None
        return store.open_until or "Ouvert"

    day = when.date().isoformat()
    minute = when.hour * 60 + when.minute
    if store.delivery_slots:
        horizon = sorted(store.delivery_slots)
        if horizon[0] <= day <= horizon[-1]:
            # Les créneaux se chevauchent (30 min tous les 15 min) : on garde celui qui
            # commence le plus près de l'heure visée.
            matching = [(s, e) for s, e in store.delivery_slots.get(day, []) if s <= minute < e]
            if not matching:
                return None
            start, end = max(matching)
            return f"Créneau {hhmm(start)}–{hhmm(end)}"
    if store.hours is not None and is_open_at(store.hours, when):
        return "Ouvert à cette heure (non planifiable : commander au moment voulu)"
    return None


def resolve_target(at: str | None, on: str | None, now: datetime) -> datetime | None:
    """"12:30" (+ date "2026-10-23" ou "demain") -> datetime Paris. Sans date : aujourd'hui,
    ou demain si l'heure est déjà passée."""
    if at is None:
        return None
    h, m = (int(x) for x in at.split(":"))
    if on in (None, ""):
        day = now.date()
    elif on.lower() == "demain":
        day = now.date() + timedelta(days=1)
    else:
        day = date.fromisoformat(on)
    target = datetime(day.year, day.month, day.day, h, m, tzinfo=PARIS)
    if on in (None, "") and target < now - timedelta(minutes=NOW_MARGIN_MIN):
        target += timedelta(days=1)
    return target
