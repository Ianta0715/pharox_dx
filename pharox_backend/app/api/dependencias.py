"""
Dependencias inyectables de FastAPI. Los tests las reemplazan con
app.dependency_overrides para correr la API sin Neo4j ni Ollama.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException
from neo4j.exceptions import ServiceUnavailable

from app.ai_gateway import DEFAULT_MODEL, generar_embedding, get_llm
from app.config import get_settings
from app.conocimiento.grafo import Lector, leer
from app.logging_config import get_logger
from app.servicios.copiloto import Dependencias

logger = get_logger("pharox.api")


def obtener_lector() -> Lector:
    return leer


def _llm_redaccion():
    # Redactar es reformular material ya resuelto: no necesita el modo de
    # razonamiento de qwen3, que además filtraba "<think>" al contenido.
    return get_llm(task="sintesis", data_sensitive=True, temperature=0.1, reasoning=False, num_predict=1200)


def _llm_cypher():
    # Traducción corta y mecánica: sin razonamiento y con tope bajo de tokens,
    # porque qwen3 llegó a entrar en loop sobre un caso clínico largo.
    return get_llm(task="cypher", data_sensitive=True, temperature=0.0, reasoning=False, num_predict=400, repeat_penalty=1.3)


def obtener_dependencias() -> Dependencias:
    return Dependencias(
        lector=leer,
        embedder=generar_embedding,
        crear_llm=_llm_redaccion,
        crear_llm_cypher=_llm_cypher,
        nombre_modelo=DEFAULT_MODEL,
        verificacion_estricta=get_settings().verificacion_estricta,
    )


def obtener_llm_cypher() -> Callable[[], Any]:
    return _llm_cypher


def error_interno(accion: str, error: Exception) -> HTTPException:
    """Loguea el detalle y devuelve un error sin filtrar internals (antes se devolvía repr(e) al cliente)."""
    if isinstance(error, ServiceUnavailable):
        logger.error(f"Neo4j no disponible al {accion}: {error}")
        return HTTPException(status_code=503, detail="La base de conocimiento (Neo4j) no está disponible.")
    logger.exception(f"Error al {accion}: {error}")
    return HTTPException(status_code=500, detail=f"Error interno al {accion}. Ver logs del servidor.")
