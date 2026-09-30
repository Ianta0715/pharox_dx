"""
Interpretación de una consulta: ¿describe un caso o es una pregunta general?

El copiloto responde dos tipos de preguntas, igual que antes del motor de reglas:

- CASO: la consulta describe a una paciente ("54 años, cT2 cN1 M0, RE 90 %,
  HER2 2+..."). Va al motor de reglas, que evalúa el catálogo sobre ese perfil,
  y la evidencia se busca dirigida por lo que el motor dejó abierto.
- GENERAL: pregunta de conocimiento o de cohorte ("¿qué evidencia hay para
  PIK3CA H1047R?", "¿cuántas pacientes triple negativo hay en el registro?").
  No hay a quién aplicarle el catálogo: la respuesta sale de la base de
  conocimiento (text-to-Cypher + fuentes del grafo).

La decisión es determinística y se informa en la respuesta. Criterio: cuántos
fragmentos DISTINTOS del texto aportaron datos clínicos. "Triple negativo" fija
tres campos pero es una sola mención; "54 años, cT2 cN1 M0, RE 90 %" son varias.
Se puede forzar con `modo`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from app.dominio.extraccion import Extraccion, extraer_perfil
from app.dominio.farmacos import farmacos_mencionados

Modo = Literal["caso", "general"]
ModoPedido = Literal["auto", "caso", "general"]

MINIMO_FRAGMENTOS_CASO = 3

_TEMAS = {
    "ensayos": r"\b(ensayos?|trials?|estudios? clinicos?|reclutando|reclutamiento|nct\d*)\b",
    "registro": r"\b(registro|cohorte|pacientes|casos|hospital|mendoza|poblacion)\b",
    "protocolos": r"\b(protocolos?|esquemas?|tratamiento estandar|regimen(es)?)\b",
    "guias": r"\b(guias?|asco|esmo|nccn|actualizacion(es)?|consenso)\b",
}


@dataclass(frozen=True)
class Interpretacion:
    extraccion: Extraccion
    farmacos: tuple[str, ...]
    temas: frozenset[str]
    modo: Modo
    motivo_modo: str


def fragmentos_clinicos(extraccion: Extraccion) -> int:
    return len({t.fragmento for t in extraccion.trazas})


def _normalizar(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))


def temas_de(texto: str | None) -> frozenset[str]:
    norm = _normalizar(texto or "")
    return frozenset(tema for tema, patron in _TEMAS.items() if re.search(patron, norm))


def decidir_modo(extraccion: Extraccion, pedido: ModoPedido = "auto", hay_perfil_estructurado: bool = False) -> tuple[Modo, str]:
    if pedido != "auto":
        return pedido, f"modo '{pedido}' pedido explícitamente"
    if hay_perfil_estructurado:
        return "caso", "se envió un perfil clínico estructurado"
    n = fragmentos_clinicos(extraccion)
    tiene_edad = extraccion.perfil.edad is not None
    if n >= MINIMO_FRAGMENTOS_CASO or (tiene_edad and n >= 2):
        return "caso", f"la consulta describe un caso ({n} datos clínicos reconocidos)"
    return "general", f"consulta general: no describe un caso concreto ({n} dato(s) clínico(s) reconocido(s))"


def interpretar(texto: str | None, pedido: ModoPedido = "auto", hay_perfil_estructurado: bool = False) -> Interpretacion:
    extraccion = extraer_perfil(texto)
    modo, motivo = decidir_modo(extraccion, pedido, hay_perfil_estructurado)
    return Interpretacion(
        extraccion=extraccion,
        farmacos=farmacos_mencionados(texto),
        temas=temas_de(texto),
        modo=modo,
        motivo_modo=motivo,
    )
