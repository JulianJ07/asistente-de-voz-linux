from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from .config import AssistantError, Config


def screenshot_command(path: Path) -> list[str] | None:
    spectacle = shutil.which("spectacle")
    if spectacle:
        return [spectacle, "-b", "-n", "-f", "-o", str(path)]

    grim = shutil.which("grim")
    if grim:
        return [grim, str(path)]

    gnome_screenshot = shutil.which("gnome-screenshot")
    if gnome_screenshot:
        return [gnome_screenshot, "-f", str(path)]

    imagemagick_import = shutil.which("import")
    if imagemagick_import:
        return [imagemagick_import, "-window", "root", str(path)]

    return None


def capture_screen(cfg: Config) -> Path:
    command = screenshot_command(cfg.screen_path)
    if not command:
        raise AssistantError("No hay herramienta de captura. Instala spectacle, grim o gnome-screenshot.")

    cfg.screen_path.unlink(missing_ok=True)
    try:
        subprocess.run(command, check=True, timeout=12, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except Exception as exc:
        raise AssistantError(f"Fallo la captura de pantalla con {command[0]}.") from exc

    for _ in range(20):
        if cfg.screen_path.exists() and cfg.screen_path.stat().st_size > 0:
            return cfg.screen_path
        time.sleep(0.1)

    if not cfg.screen_path.exists() or cfg.screen_path.stat().st_size == 0:
        raise AssistantError("La captura de pantalla quedo vacia.")

    return cfg.screen_path


def check_screen_capture(cfg: Config) -> tuple[bool, str]:
    command = screenshot_command(cfg.screen_path)
    if not command:
        return False, "FALTA spectacle, grim, gnome-screenshot o import"

    try:
        path = capture_screen(cfg)
        return True, f"OK ({command[0]} -> {path})"
    except Exception as exc:
        return False, f"ERROR ({exc})"
