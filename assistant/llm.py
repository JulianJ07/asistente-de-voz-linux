from __future__ import annotations

from pathlib import Path

from . import codex_cli, reasoning
from .config import AssistantError, Config


SYSTEM_TEXT = (
    "Eres un asistente de voz para Linux llamado J.A.R.V.I.S. "
    "Responde siempre en español latino neutro. "
    "Tono: formal, preciso, levemente seco. Como un asistente altamente competente. "
    "Por defecto responde en una frase corta, máximo dos frases. "
    "Nunca uses signos de exclamación. Evita palabras como 'claro', 'por supuesto', 'genial'. "
    "Usa frases como 'Entendido.', 'Hecho.', 'Procesando.', 'Detecté X, procediendo.'. "
    "Limita la respuesta a 30 palabras salvo que el usuario pida detalle. "
    "No inventes acciones ejecutadas."
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
