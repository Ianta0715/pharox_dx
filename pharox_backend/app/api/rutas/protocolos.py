"""Vigilancia de protocolos ASCO/ESMO (Pharox_Documento_v5 §11 Capa 2)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from app.api.dependencias import error_interno, obtener_lector
from app.api.esquemas import ActualizacionesProtocoloResponse
from app.api.rutas.registro import validar_subtipo
from app.api.seguridad import verificar_api_key
from app.conocimiento.fuentes import protocolos
from app.conocimiento.grafo import Lector

router = APIRouter(prefix="/api/v1/protocolos", tags=["protocolos"], dependencies=[Depends(verificar_api_key)])


@router.get("/actualizaciones", response_model=ActualizacionesProtocoloResponse)
def actualizaciones(subtipo_molecular: Optional[str] = None, limit: int = 20, lector: Lector = Depends(obtener_lector)):
    """Actualizaciones de guías de mama más recientes, filtrables por subtipo molecular."""
    validar_subtipo(subtipo_molecular)
    try:
        filas = protocolos.listar_actualizaciones(lector, subtipo_molecular, limit)
    except Exception as e:
        raise error_interno("leer actualizaciones de protocolo", e)
    return {"success": True, "total": len(filas), "actualizaciones": filas}
