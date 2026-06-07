from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import AssistantError, Config


LOGIN_HINTS = [
    "not logged in",
    "login required",
    "please login",
    "authentication",
    "auth",
    "sign in",
    "inicia sesion",
]


def _base_command(cfg: Config) -> list[str]:
    parts = shlex.split(cfg.codex_command)
    if not parts:
        raise AssistantError("CODEX_COMMAND esta vacio. Usa CODEX_COMMAND=codex.")

    executable = shutil.which(parts[0])
    if not executable:
        raise AssistantError(f"No encontre Codex CLI: {parts[0]}. Instala o ajusta CODEX_COMMAND.")

    parts[0] = executable
    if "exec" not in parts[1:]:
        parts.append("exec")
    return parts


def _build_command(cfg: Config, output_path: Path, image_path: Path | None = None) -> list[str]:
    command = _base_command(cfg)
    command += [
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
        "-s",
        cfg.codex_sandbox,
        "-o",
        str(output_path),
    ]
    if cfg.codex_bypass_approvals_and_sandbox:
        command.append("--dangerously-bypass-approvals-and-sandbox")

    if cfg.codex_model:
        command += ["-m", cfg.codex_model]
    if cfg.codex_reasoning_level:
        command += ["-c", f'model_reasoning_effort="{cfg.codex_reasoning_level}"']
    if image_path:
        command += ["-i", str(image_path)]

    command.append("-")
    return command


def _clean_output(stdout: str, stderr: str, output_path: Path) -> str:
    if output_path.exists():
        text = output_path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return text

    lines = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("{") and line.endswith("}"):
            continue
        if line.lower().startswith(("codex", "warning:", "error:")):
            continue
        lines.append(line)

    text = "\n".join(lines).strip()
    if text:
        return text
    return stderr.strip()


def ask(prompt: str, cfg: Config, image_path: Path | None = None) -> str:
    with tempfile.NamedTemporaryFile(prefix="codex-last-message-", suffix=".txt", delete=False) as tmp:
        output_path = Path(tmp.name)

    command = _build_command(cfg, output_path, image_path=image_path)
    try:
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=cfg.codex_timeout_seconds,
            cwd=str(cfg.root),
        )
    except subprocess.TimeoutExpired as exc:
        output_path.unlink(missing_ok=True)
        raise AssistantError(f"Codex CLI no respondio en {cfg.codex_timeout_seconds} segundos.") from exc
    except FileNotFoundError as exc:
        output_path.unlink(missing_ok=True)
        raise AssistantError("No encontre Codex CLI. Instala Codex o ajusta CODEX_COMMAND.") from exc
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise AssistantError("Fallo al ejecutar Codex CLI.") from exc

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    combined = f"{stdout}\n{stderr}".lower()
    answer = _clean_output(stdout, stderr, output_path).strip()
    output_path.unlink(missing_ok=True)

    if result.returncode != 0:
        if any(hint in combined for hint in LOGIN_HINTS):
            raise AssistantError("Codex CLI requiere login. Ejecuta: codex login")
        detail = answer or f"codigo de salida {result.returncode}"
        raise AssistantError(f"Codex CLI fallo: {detail}")

    if any(hint in combined for hint in LOGIN_HINTS):
        raise AssistantError("Codex CLI parece pedir login. Ejecuta: codex login")

    if not answer:
        raise AssistantError("Codex CLI devolvio una respuesta vacia.")

    return answer


def check(cfg: Config) -> tuple[bool, str]:
    try:
        answer = ask("Responde únicamente: OK", cfg).strip()
    except AssistantError as exc:
        return False, f"ERROR ({exc})"

    if "OK" not in answer.upper():
        return False, f"RESPUESTA INESPERADA ({answer[:120]})"
    return True, "OK"
