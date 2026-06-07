from __future__ import annotations

from pathlib import Path

from . import codex_cli, reasoning
from .config import AssistantError, Config


SYSTEM_TEXT = (
    "Eres un asistente de voz para Linux. Responde siempre en español latino. "
    "Se natural, directo y preciso. Por defecto responde en una frase corta; usa maximo dos frases si hace falta. "
    "Limita la respuesta a unas 35 palabras salvo que el usuario pida detalle, explicacion larga o pasos. "
    "Si pide pasos, da maximo cuatro salvo que solicite mas detalle explicitamente. "
    "Mantén un tono tecnologico, calmado y cercano. "
    "No inventes acciones ejecutadas: si solo estas respondiendo, dilo como explicacion."
)


VISION_TEXT = (
    "Eres un asistente de vision para Linux. Analiza la captura de pantalla y responde en español latino. "
    "Se amable y concreto: di que se ve, identifica errores visibles y sugiere el siguiente paso si aplica. "
    "Responde en maximo tres frases cortas. "
    "Se concreto y evita asumir datos que no aparecen en la imagen."
)


def _ensure_codex_backend(cfg: Config) -> None:
    if cfg.llm_backend != "codex_cli":
        raise AssistantError(f"Backend LLM no soportado: {cfg.llm_backend}. Usa LLM_BACKEND=codex_cli.")


def ask_text(question: str, cfg: Config) -> str:
    _ensure_codex_backend(cfg)
    cfg, _level = reasoning.consume_config(cfg)
    prompt = f"{SYSTEM_TEXT}\n\nUsuario:\n{question}\n\nResponde solo con la respuesta final para voz."
    return codex_cli.ask(prompt, cfg)


def ask_vision(question: str, image_path: Path, cfg: Config) -> str:
    _ensure_codex_backend(cfg)
    cfg, _level = reasoning.consume_config(cfg)
    prompt = (
        f"{VISION_TEXT}\n\n"
        f"Imagen adjunta: {image_path}\n"
        f"Pregunta del usuario: {question}\n\n"
        "Responde solo con la respuesta final para voz."
    )
    return codex_cli.ask(prompt, cfg, image_path=image_path)
