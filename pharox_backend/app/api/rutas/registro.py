"""
Registro real de tumores (Hospital Central - Mendoza), cohorte y elegibilidad
a ensayos ya persistida.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencias import error_interno, obtener_lector
from app.api.esquemas import (
    ElegibilidadPacienteResponse,
    ProtocolosEstandarResponse,
    RegistroTumoresResponse,
    ResumenCohorteRealResponse,
)
from app.api.seguridad import verificar_api_key
from app.conocimiento.fuentes import ensayos, protocolos, registro
from app.conocimiento.grafo import Lector
from app.dominio.perfil import SUBTIPOS

router = APIRouter(prefix="/api/v1", tags=["registro"], dependencies=[Depends(verificar_api_key)])

SUBTIPOS_VALIDOS = set(SUBTIPOS) | {"desconocido"}


def validar_subtipo(subtipo: str | None) -> None:
    if subtipo and subtipo not in SUBTIPOS_VALIDOS:
        raise HTTPException(status_code=422, detail=f"subtipo_molecular inválido. Valores permitidos: {sorted(SUBTIPOS_VALIDOS)}")


@router.get("/registro_tumores", response_model=RegistroTumoresResponse)
def listar_registros(subtipo_molecular: Optional[str] = None, limit: int = 50, lector: Lector = Depends(obtener_lector)):
    """Pacientes reales de mama (:RegistroTumor), para un selector en el front."""
    validar_subtipo(subtipo_molecular)
    try:
        filas = registro.listar_registros(lector, subtipo_molecular, limit)
    except Exception as e:
        raise error_interno("leer registros de tumor", e)
    return {"success": True, "total": len(filas), "registros": filas}


@router.get("/registro_tumores/{paciente_id}/protocolo_estandar", response_model=ProtocolosEstandarResponse)
def protocolo_estandar(paciente_id: str, lector: Lector = Depends(obtener_lector)):
    """Protocolos estándar del subtipo de un paciente real (referencia, no el tratamiento que recibió)."""
    try:
        subtipo = registro.obtener_subtipo(lector, paciente_id)
        if subtipo is None:
            raise HTTPException(status_code=404, detail=f"No se encontró el paciente {paciente_id}.")
        filas = protocolos.protocolo_estandar_por_subtipo(lector, subtipo)
    except HTTPException:
        raise
    except Exception as e:
        raise error_interno("leer el protocolo estándar", e)
    return {"success": True, "subtipo_molecular": subtipo, "total": len(filas), "protocolos": filas}


@router.get("/cohorte/resumen", response_model=ResumenCohorteRealResponse)
def resumen_cohorte(lector: Lector = Depends(obtener_lector)):
    """Registro local (Mendoza) + frecuencias génicas de cBioPortal, agregados."""
    try:
        return {"success": True, **registro.resumen_cohorte_real(lector)}
    except Exception as e:
        raise error_interno("leer el resumen de cohorte", e)


@router.get("/pacientes/{paciente_id}/elegibilidad_trials", response_model=ElegibilidadPacienteResponse)
def elegibilidad_trials(paciente_id: str, lector: Lector = Depends(obtener_lector)):
    """Elegibilidad a ensayos ya calculada por app/gold/reglas_elegibilidad_trials.py (lectura directa)."""
    try:
        filas = ensayos.elegibilidad_persistida(lector, paciente_id)
    except Exception as e:
        raise error_interno("leer la elegibilidad a ensayos", e)
    return {"success": True, "paciente_id": paciente_id, "total": len(filas), "ensayos": filas}
