"""
Gateway único de modelos de lenguaje (Pharox_Documento_v5 §13): los agentes y
cadenas piden un modelo por tarea, no por proveedor concreto -- cambiar de
proveedor es cambiar configuración, no reescribir código.

Hoy solo hay un proveedor implementado (Ollama local, `qwen3:8b`), pero el
contrato ya separa "qué tarea" de "qué proveedor la resuelve" para que sumar
un proveedor cloud (Anthropic/OpenAI) el día que haya API key sea agregar un
branch en `_build_llm`, no tocar `main.py` / `etl_casos_clinicos.py`.

Soberanía de datos: `data_sensitive` (default `True`) fuerza el proveedor
local sin importar `TASK_PROVIDERS` -- ninguna tarea que procese texto clínico
real de un paciente (síntesis, extracción de informes) debe poder salir a una
API externa por un error de configuración. Solo tareas que no tocan dato de
paciente (ej. futuras tareas sobre conocimiento público ya publicado) pueden
pasar `data_sensitive=False` para aprovechar un modelo cloud más capaz.
"""
import os
from langchain_ollama import ChatOllama, OllamaEmbeddings

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

# Proveedor por tarea, override-able por variable de entorno. Todas en "ollama"
# hasta que se implemente un proveedor cloud real en _build_llm.
TASK_PROVIDERS = {
    "cypher": os.getenv("AI_PROVIDER_CYPHER", "ollama"),
    "sintesis": os.getenv("AI_PROVIDER_SINTESIS", "ollama"),
    "extraccion": os.getenv("AI_PROVIDER_EXTRACCION", "ollama"),
    "default": os.getenv("AI_PROVIDER_DEFAULT", "ollama"),
}


def get_llm(task: str = "default", data_sensitive: bool = True, temperature: float = 0.0, **kwargs):
    """
    Devuelve el chat model configurado para `task`.

    `data_sensitive=True` (default) ignora TASK_PROVIDERS y fuerza "ollama".
    Pasar `data_sensitive=False` solo para tareas que razonan exclusivamente
    sobre conocimiento público ya publicado (nunca sobre datos de un paciente).

    `**kwargs` se reenvían tal cual al cliente del proveedor (p. ej.
    `reasoning`, `num_predict`, `repeat_penalty` para Ollama) -- quien arma la
    cadena en main.py decide esos parámetros por tarea, ai_gateway solo los
    transporta, para no hardcodear acá conocimiento de qué tarea necesita qué.
    """
    provider = "ollama" if data_sensitive else TASK_PROVIDERS.get(task, TASK_PROVIDERS["default"])
    return _build_llm(provider, temperature=temperature, **kwargs)


def _build_llm(provider: str, temperature: float, **kwargs):
    if provider == "ollama":
        # `model` en kwargs permite que una tarea puntual use un modelo Ollama
        # distinto de DEFAULT_MODEL (p. ej. una tarea que necesita mas
        # capacidad de razonamiento que otra) sin agregar un segundo enum de
        # proveedor -- sigue siendo el mismo proveedor "ollama", solo cambia
        # el nombre del modelo que Ollama sirve.
        model = kwargs.pop("model", DEFAULT_MODEL)
        return ChatOllama(model=model, base_url=OLLAMA_HOST, temperature=temperature, **kwargs)
    raise ValueError(
        f"Proveedor '{provider}' configurado pero no implementado en ai_gateway.py "
        f"(_build_llm). Proveedores disponibles hoy: ollama."
    )


def get_embeddings(model: str = "nomic-embed-text") -> OllamaEmbeddings:
    """
    Embeddings para búsqueda vectorial (Literatura, CasoClinico). Sin routing
    todavía: nomic-embed-text local es el único modelo de embeddings en uso.
    """
    return OllamaEmbeddings(model=model, base_url=OLLAMA_HOST)


def llamar_modelo(prompt: str, task: str = "default", data_sensitive: bool = True) -> str:
    """Mantiene compatibilidad con firmas de llamadas anteriores (prompt -> texto)."""
    llm = get_llm(task=task, data_sensitive=data_sensitive)
    response = llm.invoke(prompt)
    return response.content


def generar_embedding(texto: str, model: str = "nomic-embed-text") -> list[float]:
    """Mantiene compatibilidad con firmas de llamadas anteriores."""
    embeddings = get_embeddings(model=model)
    return embeddings.embed_query(texto)
