from datetime import datetime

import pytest

from dailyuber.hours import (PARIS, availability, is_open_at, parse_day_range, parse_delivery_slots,
                             parse_hours, resolve_target)
from dailyuber.models import Store

THU_1245 = datetime(2026, 10, 22, 12, 45, tzinfo=PARIS)  # jeudi


def at(day: int, hh: int, mm: int = 0) -> datetime:
    """Datetime de la semaine du lundi 19/10/2026 : day 0 = lundi."""
    return datetime(2026, 10, 19 + day, hh, mm, tzinfo=PARIS)


@pytest.mark.parametrize("text,expected", [
    ("Tous les jours", set(range(7))),
    ("Lundi - Vendredi", {0, 1, 2, 3, 4}),
    ("Dimanche - Jeudi", {6, 0, 1, 2, 3}),          # passe par le week-end
    ("Samedi", {5}),
    ("Lundi, Mercredi et Vendredi", {0, 2, 4}),
    ("Jours fériés", None),
])
def test_parse_day_range(text, expected):
    assert parse_day_range(text) == expected


def test_parse_hours_and_is_open_at():
    weekly = parse_hours([
        {"dayRange": "Lundi - Vendredi", "sectionHours": [{"startTime": 690, "endTime": 870},
                                                          {"startTime": 1110, "endTime": 1350}]},
        {"dayRange": "Samedi", "sectionHours": []},
    ])
    assert is_open_at(weekly, at(3, 12, 30))       # jeudi midi
    assert not is_open_at(weekly, at(3, 15))       # coupure de l'après-midi
    assert is_open_at(weekly, at(3, 19))           # jeudi soir
    assert not is_open_at(weekly, at(3, 22, 30))   # 22:30 = fin exclue
    assert not is_open_at(weekly, at(5, 12))       # samedi fermé
    assert not is_open_at(weekly, at(6, 12))       # dimanche absent = fermé


def test_parse_hours_past_midnight_spills_to_next_day():
    weekly = parse_hours([{"dayRange": "Vendredi", "sectionHours": [{"startTime": 1140, "endTime": 120}]}])
    assert is_open_at(weekly, at(4, 23, 30))       # vendredi 23:30
    assert is_open_at(weekly, at(5, 1, 30))        # samedi 01:30
    assert not is_open_at(weekly, at(5, 2, 30))


def test_parse_hours_unknown_day_range_disables_hours():
    assert parse_hours([{"dayRange": "Jours fériés", "sectionHours": []}]) is None
    assert parse_hours(None) is None


def _store(**kw) -> Store:
    return Store(uuid="s", title="S", url="u", **kw)


HOURS = parse_hours([{"dayRange": "Lundi - Vendredi", "sectionHours": [{"startTime": 690, "endTime": 1170}]}])
SLOTS = parse_delivery_slots({"timeRanges": {
    "2026-10-22": [{"startTime": 810, "endTime": 840}, {"startTime": 825, "endTime": 855}],
    "2026-10-23": [{"startTime": 690, "endTime": 720}, {"startTime": 705, "endTime": 735}],
    "2026-10-26": [{"startTime": 690, "endTime": 720}],
}})


def test_availability_now_requires_open_flag_and_hours():
    s = _store(is_open=True, open_until="Ouvert jusqu'à 19:30", hours=HOURS)
    assert availability(s, None, THU_1245) == "Ouvert jusqu'à 19:30"
    assert availability(_store(is_open=False, hours=HOURS), None, THU_1245) is None
    # données en cache récupérées ouvertes, mais fermé d'après les horaires à l'heure actuelle
    assert availability(s, None, at(3, 20)) is None


def test_availability_soon_counts_as_now():
    s = _store(is_open=True, hours=HOURS, delivery_slots=SLOTS)
    assert availability(s, at(3, 13, 0), THU_1245) == "Ouvert"


def test_availability_scheduled_uses_delivery_slots():
    s = _store(is_open=False, hours=HOURS, delivery_slots=SLOTS)
    assert availability(s, at(4, 11, 40), THU_1245) == "Créneau 11:30–12:00"
    assert availability(s, at(4, 11, 50), THU_1245) == "Créneau 11:45–12:15"   # le plus proche
    assert availability(s, at(4, 14), THU_1245) is None         # date couverte, pas de créneau
    assert availability(s, at(5, 12), THU_1245) is None         # samedi : dans l'horizon, aucun créneau


def test_availability_beyond_slot_horizon_falls_back_to_hours():
    s = _store(is_open=False, hours=HOURS, delivery_slots=SLOTS)
    assert availability(s, at(8, 12), THU_1245).startswith("Ouvert à cette heure")   # mardi 27
    assert availability(s, at(12, 12), THU_1245) is None                              # samedi 31


def test_availability_without_slots_uses_hours():
    s = _store(is_open=False, hours=HOURS)
    assert availability(s, at(4, 12), THU_1245).startswith("Ouvert à cette heure")
    assert availability(s, at(4, 20), THU_1245) is None


def test_resolve_target():
    assert resolve_target(None, None, THU_1245) is None
    assert resolve_target("19:30", None, THU_1245) == at(3, 19, 30)            # plus tard aujourd'hui
    assert resolve_target("12:00", None, THU_1245) == at(4, 12)                # déjà passé -> demain
    assert resolve_target("12:30", None, THU_1245) == at(3, 12, 30)            # marge de 30 min
    assert resolve_target("12:00", "demain", THU_1245) == at(4, 12)
    assert resolve_target("20:00", "2026-10-26", THU_1245) == at(7, 20)
