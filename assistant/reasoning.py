from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

from .config import Config


OVERRIDE_PATH = Path("/tmp/voice-codex-assistant-reasoning-next.json")
OVERRIDE_TTL_SECONDS = 600

SUPPORTED_LEVELS = {"low", "medium", "high"}


def normalize_level(level: str) -> str:
    normalized = level.strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "bajo": "low",
        "low": "low",
        "medio": "medium",
        "media": "medium",
        "medium": "medium",
        "alto": "high",
        "alta": "high",
        "high": "high",
        "extremo": "high",
        "extrema": "high",
        "extremadamente-alto": "high",
        "extremadamente-alta": "high",
        "very-high": "high",
        "maximo": "high",
        "maxima": "high",
        "maximum": "high",
    }
    return aliases.get(normalized, normalized)


def display_level(level: str) -> str:
    labels = {
        "low": "bajo",
        "medium": "medio",
        "high": "alto",
    }
    return labels.get(level, level)


def save_next_override(level: str) -> str:
    normalized = normalize_level(level)
    if normalized not in SUPPORTED_LEVELS:
        raise ValueError(f"nivel de razonamiento no soportado: {level}")

    payload = {
        "created_at": time.time(),
        "level": normalized,
    }
    OVERRIDE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def load_next_override() -> str | None:
    if not OVERRIDE_PATH.exists():
        return None

    try:
        payload = json.loads(OVERRIDE_PATH.read_text(encoding="utf-8"))
        created_at = float(payload.get("created_at", 0))
        level = normalize_level(str(payload.get("level", "")))
    except Exception:
        clear_next_override()
        return None

    if time.time() - created_at > OVERRIDE_TTL_SECONDS or level not in SUPPORTED_LEVELS:
        clear_next_override()
        return None
    return level


def clear_next_override() -> None:
    OVERRIDE_PATH.unlink(missing_ok=True)


def consume_config(cfg: Config) -> tuple[Config, str | None]:
    level = load_next_override()
    if not level:
        return cfg, None

    clear_next_override()
    return replace(cfg, codex_reasoning_level=level), level
