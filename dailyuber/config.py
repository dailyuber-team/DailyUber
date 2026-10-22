import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | os.PathLike | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["discord_webhook_url"] = os.environ.get("DISCORD_WEBHOOK_URL", "")
    cfg["storage"]["data_dir"] = str(ROOT / cfg["storage"]["data_dir"])
    return cfg
