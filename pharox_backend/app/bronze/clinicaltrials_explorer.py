"""
clinicaltrials_explorer.py
Cliente de exploracion para la API REST v2 de ClinicalTrials.gov (NLM/NIH).

Objetivo: traer ensayos clinicos de cancer de mama (por defecto, en reclutamiento)
con los campos necesarios para contrastar el caso de una paciente contra alternativas
de tratamiento respaldadas por un ensayo activo: condicion, fase, intervenciones,
sponsor, resumen y paises donde reclutan.

A diferencia de CIViC, esta API si permite filtrar por condicion y estado del lado
del servidor (query.cond / filter.overallStatus), asi que no hace falta un filtro
de enfermedad posterior a la descarga.

Uso:
    python -m app.bronze.clinicaltrials_explorer
    python -m app.bronze.clinicaltrials_explorer "breast cancer" RECRUITING
    python -m app.bronze.clinicaltrials_explorer "HER2 positive breast cancer" ""

No requiere API key: la API v2 de ClinicalTrials.gov es publica y sin autenticacion.
"""
import os
import sys
import json
from datetime import datetime, timezone

import requests

CTGOV_API_URL = "https://clinicaltrials.gov/api/v2/studies"

# Carpeta bronze de datos crudos (misma capa que civic_explorer.py)
SALIDA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "bronze"
)

# Subconjunto de campos que necesitamos (evita traer el registro completo, que
# incluye modulos de resultados/referencias mucho mas pesados que no usamos hoy).
# Los campos de elegibilidad (EligibilityCriteria/Sex/MinimumAge/MaximumAge/
# HealthyVolunteers) alimentan la capa de reglas de elegibilidad -- ver
# app/gold/reglas_elegibilidad_trials.py. "StdAges" NO es un nombre de campo
# valido para la API (probado en vivo contra /api/v2/studies), por eso no esta.
CAMPOS = ",".join([
    "NCTId", "BriefTitle", "OverallStatus", "Phase", "Condition",
    "InterventionName", "LeadSponsorName", "BriefSummary", "LocationCountry",
    "EligibilityCriteria", "Sex", "MinimumAge", "MaximumAge", "HealthyVolunteers",
])

PAGE_SIZE = 200


def consultar_pagina(condicion: str, estado: str, page_token: str = None) -> dict:
    """Ejecuta una pagina de busqueda contra la API v2 de ClinicalTrials.gov."""
    params = {
        "query.cond": condicion,
        "fields": CAMPOS,
        "pageSize": PAGE_SIZE,
        "format": "json",
    }
    if estado:
        params["filter.overallStatus"] = estado
    if page_token:
        params["pageToken"] = page_token

    respuesta = requests.get(CTGOV_API_URL, params=params, timeout=30)
    respuesta.raise_for_status()
    return respuesta.json()


def buscar_ensayos(condicion: str = "breast cancer", estado: str = "RECRUITING", max_ensayos: int = 1000) -> list[dict]:
    """
    Pagina sobre la API de ClinicalTrials.gov hasta agotar resultados o alcanzar
    max_ensayos. Devuelve la lista cruda de estudios (nivel protocolSection).
    """
    estudios: list[dict] = []
    page_token = None

    while True:
        payload = consultar_pagina(condicion, estado, page_token)
        estudios.extend(payload.get("studies", []))
        print(f"[ClinicalTrials.gov] {len(estudios)} ensayo(s) descargado(s) hasta ahora...")

        page_token = payload.get("nextPageToken")
        if not page_token or len(estudios) >= max_ensayos:
            break

    return estudios[:max_ensayos]


def guardar_json(estudios: list[dict], condicion: str, estado: str) -> str:
    """Persiste la respuesta cruda de ClinicalTrials.gov en la capa bronze."""
    os.makedirs(SALIDA_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug_estado = estado.lower() if estado else "todos"
    nombre_archivo = f"clinicaltrials_{slug_estado}_{timestamp}.json".replace(" ", "_")
    ruta = os.path.join(SALIDA_DIR, nombre_archivo)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(estudios, f, indent=2, ensure_ascii=False)
    return ruta


def main():
    condicion = sys.argv[1] if len(sys.argv) > 1 else "breast cancer"
    estado = sys.argv[2] if len(sys.argv) > 2 else "RECRUITING"

    print(f"Consultando ClinicalTrials.gov: condicion='{condicion}', estado='{estado or 'CUALQUIERA'}'...")
    estudios = buscar_ensayos(condicion, estado)

    print(f"\nTotal de ensayos descargados: {len(estudios)}")
    if estudios:
        primero = estudios[0].get("protocolSection", {}).get("identificationModule", {})
        print(f"Ejemplo: {primero.get('nctId')} - {primero.get('briefTitle')}")

    ruta = guardar_json(estudios, condicion, estado)
    print(f"\nJSON crudo guardado en: {ruta}")


if __name__ == "__main__":
    main()
