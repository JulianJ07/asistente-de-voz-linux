from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config


PENDING_PATH = Path("/tmp/voice-codex-assistant-pending.json")
PENDING_TTL_SECONDS = 300


DANGEROUS_PATTERNS = [
    r"\brm\s+(-[^\s]*[rf][^\s]*|-[^\s]*[fr][^\s]*)\b",
    r"\bmkfs(\.|s|\b)",
    r"\bdd\s+.*\bof=/dev/",
    r"\b(shred|wipefs)\b",
    r"\b(fdisk|cfdisk|parted|gdisk|sgdisk)\b",
    r"\bcryptsetup\b",
    r"\bmount\s+.*\s/\s*$",
    r"\bchmod\s+-R\s+777\s+/",
    r"\bchown\s+-R\s+.*\s+/",
    r"\b(userdel|groupdel)\b",
    r"\bvisudo\b",
    r"\b/etc/sudoers\b",
    r"\bsystemctl\s+(disable|mask)\b",
    r"\bpacman\s+-R",
]


@dataclass(frozen=True)
class SafetyDecision:
    uses_sudo: bool
    dangerous: bool
    needs_confirmation: bool
    reason: str


def normalize_command(command: str) -> str:
    return " ".join(command.strip().split())


def uses_sudo(command: str) -> bool:
    return normalize_command(command).startswith("sudo ")


def is_dangerous(command: str) -> tuple[bool, str]:
    normalized = normalize_command(command)
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return True, f"coincide con patron peligroso: {pattern}"
    return False, ""


def sudo_nopasswd_available() -> bool:
    try:
        result = subprocess.run(["sudo", "-n", "true"], timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except Exception:
        return False


def assess_command(command: str, cfg: Config) -> SafetyDecision:
    sudo = uses_sudo(command)
    dangerous, reason = is_dangerous(command)
    needs_confirmation = False if cfg.disable_safety_confirmations else dangerous or (sudo and cfg.require_confirmation_for_sudo)
    if sudo and not reason:
        reason = "comando con sudo"
    if not reason:
        reason = "comando normal"
    return SafetyDecision(
        uses_sudo=sudo,
        dangerous=dangerous,
        needs_confirmation=needs_confirmation,
        reason=reason,
    )


def save_pending(command: str, mode: str, description: str, reason: str) -> None:
    payload = {
        "created_at": time.time(),
        "command": command,
        "mode": mode,
        "description": description,
        "reason": reason,
    }
    PENDING_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_pending() -> dict | None:
    if not PENDING_PATH.exists():
        return None
    try:
        payload = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except Exception:
        clear_pending()
        return None

    if time.time() - float(payload.get("created_at", 0)) > PENDING_TTL_SECONDS:
        clear_pending()
        return None
    return payload


def clear_pending() -> None:
    PENDING_PATH.unlink(missing_ok=True)


def is_confirmation(text: str) -> bool:
    lowered = text.strip().lower()
    return any(phrase in lowered for phrase in ["confirma ejecutar", "confirmo ejecutar", "confirmar ejecutar"])


def is_cancel(text: str) -> bool:
    lowered = text.strip().lower()
    return any(phrase in lowered for phrase in ["cancela", "cancelar", "no ejecutes", "no ejecutar"])


def sudo_explanation() -> str:
    return (
        "Este comando necesita sudo, pero sudo NOPASSWD no esta activo para esta sesion. "
        "Configurarlo requiere editar sudoers con visudo; no lo modifico automaticamente por seguridad."
    )
