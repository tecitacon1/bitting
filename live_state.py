"""Persistent state for the live ladder trader."""

import json
import os
from datetime import datetime, timezone

from config import LADDER_STATE_FILE


class LadderStateStore:
    def __init__(self, path=None):
        self.path = path or LADDER_STATE_FILE
        self._data = self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return self._empty()
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                return payload
        except (OSError, json.JSONDecodeError):
            pass
        return self._empty()

    @staticmethod
    def _empty():
        return {
            "slug": None,
            "local_date": None,
            "status": "idle",
            "held_bucket_temp": None,
            "held_bucket_label": None,
            "held_token_id": None,
            "held_shares": 0.0,
            "running_max_seen": None,
            "last_upgrade_at": None,
            "last_running_max_at": None,
            "peak_locked": False,
            "entry_floor": None,
            "ceiling_temp": None,
            "trades": [],
            "updated_at": None,
        }

    def save(self):
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self._data, handle, indent=2)

    def reset_for_day(self, slug, local_date, entry_floor=None, ceiling_temp=None):
        self._data = self._empty()
        self._data["slug"] = slug
        self._data["local_date"] = local_date
        self._data["entry_floor"] = entry_floor
        self._data["ceiling_temp"] = ceiling_temp
        self.save()

    def get(self):
        return dict(self._data)

    def update(self, **fields):
        self._data.update(fields)
        self.save()

    def append_trade(self, record):
        trades = list(self._data.get("trades") or [])
        trades.append(record)
        self._data["trades"] = trades[-100:]
        self.save()
