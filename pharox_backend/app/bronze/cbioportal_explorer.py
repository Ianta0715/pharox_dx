"""
cbioportal_explorer.py
Cliente de exploracion para la API REST publica de cBioPortal (cbioportal.org).

Objetivo: para un set de estudios publicos de cancer de mama, traer la frecuencia
de alteracion por gen (cuantos pacientes de la cohorte tienen mutacion, y cuantos
tienen amplificacion/deleción por copy-number) -- responde "que tan frecuente es
esta alteracion en cohortes reales de mama", sin bajar datos por muestra individual
(fuera de alcance por ahora, ver docstring de app/gold/cbioportal_to_neo4j.py).

Endpoints reales confirmados contra el Swagger en vivo (v3/api-docs), no supuestos:
    POST /api/mutated-genes/fetch  body: {"studyIds": [...]}
    POST /api/cna-genes/fetch      body: {"studyIds": [...]}
Ambos devuelven AlterationCountByGene: hugoGeneSymbol, entrezGeneId,
numberOfAlteredCases, numberOfProfiledCases (+ "alteration" en CNA: -2/-1/0/1/2).

Uso:
    python -m app.bronze.cbioportal_explorer
    python -m app.bronze.cbioportal_explorer brca_metabric brca_mskcc_2019

No requiere API key: la instancia publica de cBioPortal es de lectura libre.
"""
import os
import sys
import json
from datetime import datetime, timezone

import requests

CBIOPORTAL_API_URL = "https://www.cbioportal.org/api"

SALIDA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "bronze"
)

# Estudio publico de mama por defecto: METABRIC (2509 muestras, el mas grande y
# citado). Se puede pasar una lista mas larga por linea de comandos.
ESTUDIOS_DEFAULT = ["brca_metabric"]

# Mapeo del campo "alteration" (encoding de cBioPortal para copy-number) a un
# texto legible; ver la descripcion del perfil molecular *_cna en /molecular-profiles.
ALTERACION_CNA_LABELS = {
    2: "AMPLIFICATION",
    1: "GAIN",
    0: "NEUTRAL",
    -1: "HEMIZYGOUS_DELETION",
    -2: "DEEP_DELETION",
}


def obtener_info_estudio(study_id: str) -> dict:
    respuesta = requests.get(f"{CBIOPORTAL_API_URL}/studies/{study_id}", timeout=30)
    respuesta.raise_for_status()
    return respuesta.json()


def obtener_genes_mutados(study_id: str) -> list[dict]:
    respuesta = requests.post(
        f"{CBIOPORTAL_API_URL}/mutated-genes/fetch",
        json={"studyIds": [study_id]},
        timeout=60,
    )
    respuesta.raise_for_status()
    filas = respuesta.json()
    for fila in filas:
        fila["tipo_alteracion"] = "MUTATION"
    return filas


def obtener_genes_cna(study_id: str) -> list[dict]:
    respuesta = requests.post(
        f"{CBIOPORTAL_API_URL}/cna-genes/fetch",
        json={"studyIds": [study_id]},
        timeout=60,
    )
    respuesta.raise_for_status()
    filas = respuesta.json()
    for fila in filas:
        fila["tipo_alteracion"] = ALTERACION_CNA_LABELS.get(fila.get("alteration"), "CNA_DESCONOCIDA")
    # Las amplificaciones/deleciones son las clinicamente relevantes (ej. HER2);
    # GAIN/HEMIZYGOUS_DELETION de bajo nivel son ruido de fondo en casi todos los genes.
    return [f for f in filas if f["tipo_alteracion"] in ("AMPLIFICATION", "DEEP_DELETION")]


def explorar_estudio(study_id: str) -> dict:
    """Trae info del estudio + frecuencias de mutacion y CNA. Devuelve un solo dict por estudio."""
    print(f"[cBioPortal] Consultando estudio '{study_id}'...")
    info = obtener_info_estudio(study_id)
    mutaciones = obtener_genes_mutados(study_id)
    cna = obtener_genes_cna(study_id)
    print(f"[cBioPortal] '{study_id}': {len(mutaciones)} gen(es) con mutaciones, {len(cna)} gen(es) con CNA relevante.")

    return {
        "study_id": study_id,
        "study_info": info,
        "alteraciones": mutaciones + cna,
    }


def guardar_json(estudios: list[dict]) -> str:
    os.makedirs(SALIDA_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nombre_archivo = f"cbioportal_{timestamp}.json"
    ruta = os.path.join(SALIDA_DIR, nombre_archivo)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(estudios, f, indent=2, ensure_ascii=False)
    return ruta


def main():
    study_ids = sys.argv[1:] if len(sys.argv) > 1 else ESTUDIOS_DEFAULT

    print(f"Consultando cBioPortal para {len(study_ids)} estudio(s): {study_ids}")
    estudios = [explorar_estudio(sid) for sid in study_ids]

    ruta = guardar_json(estudios)
    print(f"\nJSON crudo guardado en: {ruta}")


if __name__ == "__main__":
    main()
