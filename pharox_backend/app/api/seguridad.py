"""
Autenticación por API key (header X-API-Key) para todos los endpoints salvo
el health check "/", que queda público para probes de infraestructura.
"""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verificar_api_key(api_key: str | None = Security(_api_key_header)) -> str:
    esperada = get_settings().pharox_api_key
    # compare_digest: comparación en tiempo constante, no filtra por timing cuántos caracteres coinciden.
    if not api_key or not esperada or not secrets.compare_digest(api_key, esperada):
        raise HTTPException(status_code=401, detail="API key inválida o faltante (header X-API-Key).")
    return api_key
