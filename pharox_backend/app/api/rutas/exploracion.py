"""
Explorador del grafo en lenguaje natural (text-to-Cypher), de solo lectura.

Devuelve el Cypher generado y las filas crudas, sin redacción. Es el mismo
paso que usa /api/v1/consultar como "consulta estructurada" (ver
servicios/exploracion.py), expuesto solo para inspeccionar la base.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencias import error_interno, obtener_lector, obtener_llm_cypher
from app.api.esquemas import ExplorarRequest, ExplorarResponse
from app.api.seguridad import verificar_api_key
from app.conocimiento.grafo import Lector
from app.logging_config import get_logger
from app.servicios.exploracion import ejecutar_pregunta

router = APIRouter(prefix="/api/v1", tags=["exploracion"], dependencies=[Depends(verificar_api_key)])
logger = get_logger("pharox.api.exploracion")

AVISO = (
    "Consulta exploratoria generada por un modelo de lenguaje: revisá el Cypher antes de sacar conclusiones. "
    "No es una evaluación clínica — para eso usá /api/v1/consultar o /api/v1/evaluar."
)


@router.post("/explorar", response_model=ExplorarResponse)
def explorar(
    payload: ExplorarRequest,
    lector: Lector = Depends(obtener_lector),
    crear_llm: Callable[[], Any] = Depends(obtener_llm_cypher),
):
    try:
        resultado = ejecutar_pregunta(payload.pregunta, lector, crear_llm())
    except ValueError as e:
        logger.warning(f"[Explorar] Cypher bloqueado: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise error_interno("ejecutar la consulta exploratoria", e)
    return {"success": True, "cypher": resultado.cypher, "filas": resultado.filas, "aviso": AVISO}
