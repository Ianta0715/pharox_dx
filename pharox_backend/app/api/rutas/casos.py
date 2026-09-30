"""Ingesta y búsqueda de casos clínicos reales anonimizados (Case-Based Reasoning)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.dependencias import error_interno, obtener_dependencias
from app.api.esquemas import BuscarCasosResponse, IngestaCasoResponse, IngestaTextoRequest
from app.api.seguridad import verificar_api_key
from app.conocimiento.fuentes import semantica
from app.logging_config import get_logger
from app.servicios.copiloto import Dependencias

router = APIRouter(prefix="/api/v1/casos", tags=["casos"], dependencies=[Depends(verificar_api_key)])
logger = get_logger("pharox.api.casos")


@router.post("/ingestar", response_model=IngestaCasoResponse)
async def ingestar(
    texto: Optional[str] = Form(None, description="Texto libre del informe anatomopatológico o descripción clínica"),
    imagen: Optional[UploadFile] = File(None, description="Imagen (JPG/PNG) del informe médico escaneado"),
):
    """
    Ingesta un caso clínico real anonimizado (hash SHA-256). Acepta texto, imagen
    (OCR) o ambos. El caso queda disponible para búsqueda por similitud.
    """
    from app.etl_casos_clinicos import procesar_informe

    if not texto and not imagen:
        raise HTTPException(status_code=400, detail="Se requiere al menos texto o imagen del informe clínico.")

    image_bytes = None
    if imagen:
        if not (imagen.content_type or "").startswith("image/"):
            raise HTTPException(status_code=422, detail=f"El archivo '{imagen.filename}' no es una imagen válida. Subí JPG o PNG.")
        image_bytes = await imagen.read()

    try:
        resultado = procesar_informe(texto=texto, image_bytes=image_bytes)
    except Exception as e:
        raise error_interno("procesar el caso clínico", e)
    logger.info(f"Caso clínico ingresado: ID={resultado['caso_id']}")
    return resultado


@router.post("/ingestar_texto", response_model=IngestaCasoResponse)
def ingestar_texto(req: IngestaTextoRequest):
    """Ingesta un caso clínico solo con texto (evita el envío de archivos vacíos desde Swagger)."""
    from app.etl_casos_clinicos import procesar_informe

    try:
        return procesar_informe(texto=req.texto, image_bytes=None)
    except Exception as e:
        raise error_interno("procesar el caso clínico", e)


@router.get("/buscar", response_model=BuscarCasosResponse)
def buscar(consulta: str, n: int = 2, deps: Dependencias = Depends(obtener_dependencias)):
    """Casos clínicos reales similares a un texto (búsqueda vectorial)."""
    if deps.embedder is None:
        raise HTTPException(status_code=503, detail="Búsqueda vectorial no disponible (sin embeddings).")
    try:
        casos = semantica.buscar_casos_similares(deps.lector, deps.embedder(consulta), n)
    except Exception as e:
        raise error_interno("buscar casos similares", e)
    textos = [semantica.formatear_caso(c) for c in casos]
    return {"success": True, "casos_encontrados": len(textos), "casos": textos}
