"""Health check, estado del grafo y catálogo de reglas (auditoría)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencias import error_interno, obtener_lector
from app.api.esquemas import DebugGraphResponse, HealthResponse, ReglasResponse
from app.api.serializacion import catalogo_a_salida
from app.api.seguridad import verificar_api_key
from app.conocimiento.fuentes.estado import estado_grafo
from app.conocimiento.grafo import Lector

publico = APIRouter(tags=["sistema"])
router = APIRouter(prefix="/api/v1", tags=["sistema"], dependencies=[Depends(verificar_api_key)])


@publico.get("/", response_model=HealthResponse)
def health_check():
    """Público (sin API key), para probes de infraestructura."""
    return {"status": "ok", "service": "Pharox DX - motor de reglas + grafo de conocimiento"}


@router.get("/reglas", response_model=ReglasResponse)
def reglas():
    """Catálogo completo de reglas clínicas que aplica el motor, con fuente y versión, para auditoría del comité."""
    return catalogo_a_salida()


@router.get("/debug/graph_db", response_model=DebugGraphResponse)
def debug_graph_db(lector: Lector = Depends(obtener_lector)):
    try:
        return {"success": True, "estado": estado_grafo(lector)}
    except Exception as e:
        raise error_interno("leer el estado del grafo", e)
