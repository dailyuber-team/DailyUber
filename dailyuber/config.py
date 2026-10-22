import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | os.PathLike | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Une config antérieure à l'option Uber One reste valable (abonnement désactivé).
    cfg["fees"]["uber_one"] = {"enabled": False, "min_subtotal": 12.0, "service_fee_discount": 0.5,
                               **(cfg["fees"].get("uber_one") or {})}
    cfg["discord_webhook_url"] = os.environ.get("DISCORD_WEBHOOK_URL", "")
    cfg["storage"]["data_dir"] = str(ROOT / cfg["storage"]["data_dir"])
    return cfg
