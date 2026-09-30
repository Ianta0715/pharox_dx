"""
Conexión a Neo4j.

Toda consulta que no escribe pasa por `leer()`, que abre la transacción en
modo LECTURA: el servidor rechaza cualquier escritura o comando de esquema
(CREATE, MERGE, SET, DELETE, DROP INDEX...) dentro de ella. Esa es la barrera
real para el Cypher que genera un LLM en /api/v1/explorar — el filtro de
palabras de lenguaje/text_to_cypher.py es solo una primera línea.

Las funciones de consulta de conocimiento/fuentes/ reciben un `Lector` (la
firma de `leer`) en vez de importarlo: así se testean con un lector falso,
sin Neo4j levantado.

Funciona igual contra la instancia AuraDB compartida (neo4j+s://...) que contra
un Neo4j local: la URI, el usuario y la base (NEO4J_DATABASE, el id de la
instancia en Aura) salen de la configuración.
"""
from __future__ import annotations

from typing import Any, Callable

from neo4j import READ_ACCESS, WRITE_ACCESS, Driver, GraphDatabase, Session

from app.config import get_settings

Lector = Callable[..., list[dict[str, Any]]]

_driver: Driver | None = None


def get_driver() -> Driver:
    """Driver compartido, creado a demanda (crearlo no abre conexión todavía)."""
    global _driver
    if _driver is None:
        s = get_settings()
        _driver = GraphDatabase.driver(
            s.neo4j_uri,
            auth=(s.neo4j_user, s.neo4j_password),
            # Fallar rápido: con la base caída (o Aura Free pausada), el default
            # (30 s de reintentos por transacción) haría esperar minutos a una
            # consulta que igual puede responder con el motor de reglas.
            connection_timeout=10.0,
            max_transaction_retry_time=5.0,
        )
    return _driver


def sesion_lectura() -> Session:
    return get_driver().session(database=get_settings().neo4j_database, default_access_mode=READ_ACCESS)


def sesion_escritura() -> Session:
    """Para la inicialización del esquema y los scripts de ingesta; nunca para consultas de la API."""
    return get_driver().session(database=get_settings().neo4j_database, default_access_mode=WRITE_ACCESS)


def cerrar_driver() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def leer(cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Ejecuta una consulta en una transacción de solo lectura y devuelve filas como dicts."""

    def _tx(tx):
        return [record.data() for record in tx.run(cypher, params or {})]

    with sesion_lectura() as session:
        return session.execute_read(_tx)
