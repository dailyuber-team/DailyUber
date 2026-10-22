"""Historique minimal : quels restaurants étaient dans le top au run précédent."""
import json
from datetime import date
from pathlib import Path

from .models import Basket


def mark_new_and_save(deals: dict[int, list[Basket]], data_dir: str, today: date | None = None) -> None:
    path = Path(data_dir) / "history.json"
    today = (today or date.today()).isoformat()
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    previous_dates = sorted(d for d in history if d < today)
    previous = history[previous_dates[-1]] if previous_dates else None

    for people, baskets in deals.items():
        before = set(previous.get(str(people), [])) if previous else None
        for b in baskets:
            b.is_new = before is not None and b.store.uuid not in before

    history[today] = {str(p): [b.store.uuid for b in bs] for p, bs in deals.items()}
    keep = sorted(history)[-30:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({d: history[d] for d in keep}, indent=1), encoding="utf-8")
