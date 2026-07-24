from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from app.profiles import MYCLAW_ROOT

logger = logging.getLogger("myclaw.preferences")


@dataclass
class UserPreferences:
    """Myclaw runtime choices kept outside Claude conversation state."""

    model: str = ""  # provider profile, for example glm or kimi
    level: str = ""  # haiku, sonnet or opus
    mode: str = ""   # h, m or l

    @property
    def complete(self) -> bool:
        return bool(self.model and self.level and self.mode)


class PreferencesManager:
    def __init__(self, state_dir: Path | None = None) -> None:
        self._state_dir = state_dir or (MYCLAW_ROOT / ".preferences")
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, open_id: str) -> Path:
        return self._state_dir / f"{open_id}.json"

    def get(self, open_id: str) -> UserPreferences:
        path = self._path(open_id)
        if not path.exists():
            return UserPreferences()
        try:
            data = json.loads(path.read_text("utf-8"))
            return UserPreferences(
                model=str(data.get("model", "")),
                level=str(data.get("level", "")),
                mode=str(data.get("mode", "")),
            )
        except Exception as exc:
            logger.warning("Failed to load preferences for %s: %s", open_id, exc)
            return UserPreferences()

    def save(self, open_id: str, preferences: UserPreferences) -> None:
        payload = json.dumps(asdict(preferences), indent=2, ensure_ascii=False)
        with self._lock:
            self._path(open_id).write_text(payload, "utf-8")

    def clear(self, open_id: str) -> UserPreferences:
        preferences = UserPreferences()
        self.save(open_id, preferences)
        return preferences


preferences_manager = PreferencesManager()
