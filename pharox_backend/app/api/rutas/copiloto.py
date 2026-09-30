"""
Copiloto clínico: motor de reglas + evidencia del grafo + redacción opcional.

- POST /api/v1/consultar — cualquier consulta clínica en texto libre (un caso o
  una pregunta general) y/o un perfil estructurado; con text-to-Cypher y
  redacción en prosa del modelo local, verificada contra la evidencia.
- POST /api/v1/evaluar — mismo contrato, sin LLM. Funciona sin Ollama.
- GET /api/v1/registro_tumores/{id}/evaluacion — evalúa una paciente real
  del registro con el mismo motor.
"""
from __future__ import annotations

import unicodedata

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencias import error_interno, obtener_dependencias
from app.api.esquemas import ConsultaRequest, ConsultaResponse
from app.api.serializacion import respuesta_a_salida
from app.api.seguridad import verificar_api_key
from app.servicios import copiloto
from app.servicios.copiloto import Dependencias

router = APIRouter(prefix="/api/v1", tags=["copiloto"], dependencies=[Depends(verificar_api_key)])

_SOLO_MAMA = "cancer de mama"


def _validar_tipo_cancer(tipo: str) -> None:
    normal ="".join(c for c in unicodedata.normalize("NFKD", tipo.lower()) if not unicodedata.combining(c))
    if normal.strip() != _SOLO_MAMA:
        raise HTTPException(status_code=422, detail="Por ahora el motor de reglas solo cubre cáncer de mama.")


def _consultar(payload: ConsultaRequest, deps: Dependencias, con_llm: bool) -> ConsultaResponse:
    _validar_tipo_cancer(payload.tipo_cancer)
    try:
        respuesta = copiloto.consultar(
            consulta=payload.consulta,
            perfil_estructurado=payload.perfil.a_dominio() if payload.perfil else None,
            deps=deps,
            narrar=con_llm and payload.narrar,
            con_evidencia=payload.incluir_evidencia,
            modo=payload.modo,
            usar_cypher=con_llm,
        )
    except Exception as e:
        raise error_interno("procesar la consulta clínica", e)
    return respuesta_a_salida(respuesta)


@router.post("/consultar", response_model=ConsultaResponse)
def consultar(payload: ConsultaRequest, deps: Dependencias = Depends(obtener_dependencias)):
    """
    Consulta clínica en lenguaje natural, como el copiloto de siempre. Si describe
    un caso, pasa por el motor de reglas; si es una pregunta general, se responde
    con la base de conocimiento (text-to-Cypher + fuentes del grafo). En ambos
    casos el modelo local redacta la respuesta y se verifica contra la evidencia.
    """
    return _consultar(payload, deps, con_llm=True)


@router.post("/evaluar", response_model=ConsultaResponse)
def evaluar(payload: ConsultaRequest, deps: Dependencias = Depends(obtener_dependencias)):
    """Mismo flujo sin ningún LLM (ni redacción ni text-to-Cypher): funciona sin Ollama."""
    return _consultar(payload, deps, con_llm=False)


@router.get("/registro_tumores/{registro_id}/evaluacion", response_model=ConsultaResponse)
def evaluar_registro(registro_id: str, incluir_evidencia: bool = True, deps: Dependencias = Depends(obtener_dependencias)):
    """Evalúa con el motor de reglas a una paciente real del registro (:RegistroTumor)."""
    try:
        respuesta = copiloto.evaluar_registro(registro_id, deps, con_evidencia=incluir_evidencia)
    except Exception as e:
        raise error_interno("evaluar el registro", e)
    if respuesta is None:
        raise HTTPException(status_code=404, detail=f"No se encontró el registro {registro_id}.")
    return respuesta_a_salida(respuesta)
