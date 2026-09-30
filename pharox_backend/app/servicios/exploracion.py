"""
Consulta estructurada al grafo en lenguaje natural (text-to-Cypher).

Es el paso 1 del copiloto anterior, conservado: el LLM local traduce la
pregunta a Cypher y las filas que devuelve Neo4j entran como una pieza más de
evidencia ("Consulta estructurada al grafo"). Cambió el cómo:
- el Cypher se valida (lenguaje/text_to_cypher.validate_cypher) y corre en una
  transacción de solo lectura, con LIMIT automático;
- si falla o no devuelve filas, la consulta sigue con el resto de las fuentes.

Lo usan el copiloto (/api/v1/consultar) y el explorador (/api/v1/explorar).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.conocimiento.esquema import describir_esquema
from app.conocimiento.evidencia import Evidencia, Fuente
from app.conocimiento.grafo import Lector
from app.lenguaje.text_to_cypher import con_limite, generar_cypher, validate_cypher

MAX_FILAS_EN_EVIDENCIA = 25
MAX_CARACTERES_EN_EVIDENCIA = 3000

# Etiquetas cuyos nodos describen personas: sus filas nunca son datos de la paciente consultada.
_ETIQUETAS_DE_PERSONAS = re.compile(r":\s*(RegistroTumor|Paciente|CasoClinico|HallazgoPatologico|EventoPostOperatorio)\b")


@dataclass(frozen=True)
class ResultadoCypher:
    cypher: str
    filas: list[dict[str, Any]]


def ejecutar_pregunta(pregunta: str, lector: Lector, llm: Any) -> ResultadoCypher:
    """Genera, valida y ejecuta el Cypher. Levanta ValueError si el Cypher es bloqueado."""
    cypher = generar_cypher(pregunta, describir_esquema(lector), llm)
    cypher = con_limite(validate_cypher(cypher))
    return ResultadoCypher(cypher=cypher, filas=lector(cypher))


def evidencia_de_cypher(resultado: ResultadoCypher) -> Evidencia | None:
    if not resultado.filas:
        return None
    filas = resultado.filas[:MAX_FILAS_EN_EVIDENCIA]
    datos = json.dumps(filas, ensure_ascii=False, default=str)
    if len(datos) > MAX_CARACTERES_EN_EVIDENCIA:
        datos = datos[:MAX_CARACTERES_EN_EVIDENCIA] + " …(truncado)"
    total = len(resultado.filas)
    return Evidencia(
        fuente=Fuente.GRAFO,
        titulo=f"{total} fila(s) devueltas por el grafo" + (f" (se muestran {len(filas)})" if total > len(filas) else ""),
        detalle=f"{datos} — Cypher: {resultado.cypher}",
        sobre_otras_personas=bool(_ETIQUETAS_DE_PERSONAS.search(resultado.cypher)),
    )
