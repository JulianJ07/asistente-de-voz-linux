from __future__ import annotations

import glob
import os
import shlex
import shutil
import sqlite3
import subprocess
import signal
from dataclasses import dataclass
from pathlib import Path

from .config import Config


@dataclass(frozen=True)
class HandyStatus:
    found: bool
    running: bool
    command: str
    history_db: Path
    message: str


def _flatpak_has(app_id: str) -> bool:
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return False
    result = subprocess.run([flatpak, "info", app_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def detect_command(cfg: Config) -> str:
    configured = Path(cfg.handy_appimage_path).expanduser()
    if configured.exists() and os.access(configured, os.X_OK):
        return shlex.quote(str(configured))

    for pattern in [
        str(Path.home() / "Downloads/Handy*_amd64.AppImage"),
        str(Path.home() / "Downloads/Handy*.AppImage"),
        str(Path.home() / "Descargas/Handy*.AppImage"),
    ]:
        matches = sorted(glob.glob(pattern), reverse=True)
        for match in matches:
            path = Path(match)
            if path.exists():
                return shlex.quote(str(path))

    for app_id in ["com.pais.handy", "io.github.cjpais.handy"]:
        if _flatpak_has(app_id):
            return f"flatpak run {app_id}"

    executable = shutil.which("handy")
    if executable:
        return shlex.quote(executable)

    return ""


def is_running() -> bool:
    patterns = [
        "Handy_.*AppImage",
        "com.pais.handy",
        "io.github.cjpais.handy",
    ]
    for pattern in patterns:
        result = subprocess.run(["pgrep", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            return True
    return False


def pids() -> list[int]:
    found: set[int] = set()
    for pattern in ["Handy_.*AppImage", "com.pais.handy", "io.github.cjpais.handy", "/handy$"]:
        result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        if result.returncode == 0:
            for raw in result.stdout.splitlines():
                raw = raw.strip()
                if raw.isdigit():
                    found.add(int(raw))
    return sorted(found)


def ensure_running(cfg: Config, logger) -> bool:
    if is_running():
        return True

    command = detect_command(cfg)
    if not command:
        logger.error("Handy no encontrado. Configura HANDY_APPIMAGE_PATH o instala Handy.")
        return False

    if not cfg.handy_autostart:
        logger.info("Handy no esta corriendo y HANDY_AUTOSTART=false.")
        return False

    logger.info("Iniciando Handy: %s", command)
    try:
        subprocess.Popen(["bash", "-lc", command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception as exc:
        logger.error("No pude iniciar Handy: %s", exc)
        return False


def trigger_recording(cfg: Config, logger) -> bool:
    """Best-effort trigger for Handy.

    The stable Wayland path is still configuring Handy's own global shortcut.
    This helper supports an explicit HANDY_TRIGGER_COMMAND, then falls back to
    SIGUSR1 because current Handy builds register SIGUSR handlers.
    """
    if cfg.handy_trigger_command:
        try:
            subprocess.Popen(["bash", "-lc", cfg.handy_trigger_command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("Ejecutado HANDY_TRIGGER_COMMAND: %s", cfg.handy_trigger_command)
            return True
        except Exception as exc:
            logger.error("Fallo HANDY_TRIGGER_COMMAND: %s", exc)
            return False

    ensure_running(cfg, logger)
    targets = pids()
    if not targets:
        logger.error("No hay proceso Handy para disparar. Configura Handy con Alt+Z o HANDY_TRIGGER_COMMAND.")
        return False

    ok = False
    for pid in targets:
        try:
            os.kill(pid, signal.SIGUSR1)
            ok = True
            logger.info("Enviado SIGUSR1 a Handy pid=%s", pid)
        except Exception as exc:
            logger.error("No pude enviar SIGUSR1 a Handy pid=%s: %s", pid, exc)
    return ok


def status(cfg: Config) -> HandyStatus:
    command = detect_command(cfg)
    running = is_running()
    found = bool(command)
    if found and running:
        message = f"OK ({command})"
    elif found:
        message = f"Encontrado, no corriendo ({command})"
    else:
        message = "FALTA. No encontre AppImage, Flatpak ni comando handy."
    return HandyStatus(found=found, running=running, command=command, history_db=cfg.handy_history_db, message=message)


def latest_history_id(cfg: Config) -> int:
    if not cfg.handy_history_db.exists():
        return 0
    try:
        with sqlite3.connect(str(cfg.handy_history_db)) as con:
            row = con.execute("select coalesce(max(id), 0) from transcription_history").fetchone()
            return int(row[0] or 0)
    except Exception:
        return 0


def new_history_entries(cfg: Config, after_id: int) -> list[tuple[int, str]]:
    if not cfg.handy_history_db.exists():
        return []
    try:
        with sqlite3.connect(str(cfg.handy_history_db)) as con:
            rows = con.execute(
                """
                select id, coalesce(post_processed_text, transcription_text)
                from transcription_history
                where id > ?
                order by id asc
                """,
                (after_id,),
            ).fetchall()
    except Exception:
        return []
    return [(int(row[0]), str(row[1]).strip()) for row in rows if str(row[1]).strip()]
