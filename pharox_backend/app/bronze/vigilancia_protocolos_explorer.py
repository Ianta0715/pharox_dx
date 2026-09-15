"""
vigilancia_protocolos_explorer.py
Cliente de exploracion para la API REST de Europe PMC, especializado en
actualizaciones de guias/protocolos de cancer de mama (ASCO, ESMO, NCCN) --
Pharox_Documento_v5 §11 "Capa 2 - Vigilancia de protocolos".

A diferencia de europepmc_explorer.py (literatura general de mama para el
indice vectorial :Literatura), esta fuente busca especificamente contenido de
tipo guia/consenso/actualizacion de practica clinica, restringido a las
revistas donde ASCO y ESMO publican sus guias (JCO, Annals of Oncology) mas
PUB_TYPE de guideline. El resultado alimenta el subgrafo independiente
:ActualizacionProtocolo (ver app/gold/vigilancia_protocolos_to_neo4j.py), no
:Literatura.

NCCN no tiene API publica ni se indexa como paper en PubMed/Europe PMC (son
guias vivas distribuidas via portal propio, no articulos de revista) -- no
esta cubierto por este script. Ver memoria "external data apis" del proyecto.

Filtra por fecha (FIRST_PDATE) para traer solo lo publicado en los ultimos
`dias_atras` dias -- correrlo periodicamente (cron o manual) es la forma de
lograr "vigilancia continua" con la infraestructura actual del proyecto (no
hay scheduler propio todavia). El gold script ingesta por MERGE (id=PMID), asi
que correr con ventanas de dias solapadas entre ejecuciones es seguro: no
duplica.

Uso:
    python -m app.bronze.vigilancia_protocolos_explorer
    python -m app.bronze.vigilancia_protocolos_explorer 90 50

No requiere API key: la API REST de Europe PMC es publica y sin autenticacion.
"""
import os
import sys
import json
from datetime import datetime, timedelta, timezone

import requests

EUROPEPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

SALIDA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "bronze"
)

# Combina: tema (mama) + señal de que es guia/consenso/actualizacion de
# practica, no un paper de investigacion primaria cualquiera.
QUERY_BASE = (
    '(breast cancer) AND '
    '(guideline OR guidelines OR consensus OR "practice guideline" OR '
    '"clinical practice guideline" OR "guideline update" OR ASCO OR ESMO) AND '
    '(JOURNAL:"Journal of Clinical Oncology" OR JOURNAL:"Annals of Oncology" OR '
    'PUB_TYPE:"Practice Guideline" OR PUB_TYPE:"Guideline" OR PUB_TYPE:"Consensus Development Conference")'
)
DIAS_ATRAS_DEFAULT = 180
PAGE_SIZE = 100


def _fecha_desde(dias_atras: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=dias_atras)).strftime("%Y-%m-%d")


def buscar_actualizaciones(dias_atras: int = DIAS_ATRAS_DEFAULT, max_articulos: int = 100) -> list[dict]:
    """
    Pagina sobre la API de Europe PMC (cursorMark) hasta agotar resultados o
    alcanzar max_articulos. Restringe a registros indexados en PubMed
    (SRC:MED) publicados desde `dias_atras` dias atras.
    """
    fecha_desde = _fecha_desde(dias_atras)
    query = f'({QUERY_BASE}) AND FIRST_PDATE:[{fecha_desde} TO 3000-01-01] AND SRC:MED'

    articulos: list[dict] = []
    cursor_mark = "*"

    while True:
        params = {
            "query": query,
            "format": "json",
            "resultType": "core",
            "pageSize": min(PAGE_SIZE, max_articulos - len(articulos)),
            "sort": "P_PDATE_D desc",
            "cursorMark": cursor_mark,
        }
        respuesta = requests.get(EUROPEPMC_SEARCH_URL, params=params, timeout=30)
        respuesta.raise_for_status()
        payload = respuesta.json()

        resultados = payload.get("resultList", {}).get("result", [])
        articulos.extend(resultados)
        print(f"[Vigilancia Protocolos] {len(articulos)} actualizacion(es) descargada(s) hasta ahora...")

        siguiente_cursor = payload.get("nextCursorMark")
        if not resultados or not siguiente_cursor or siguiente_cursor == cursor_mark or len(articulos) >= max_articulos:
            break
        cursor_mark = siguiente_cursor

    return articulos[:max_articulos]


def guardar_json(articulos: list[dict]) -> str:
    os.makedirs(SALIDA_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nombre_archivo = f"vigilancia_protocolos_{timestamp}.json"
    ruta = os.path.join(SALIDA_DIR, nombre_archivo)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(articulos, f, indent=2, ensure_ascii=False)
    return ruta


def main():
    dias_atras = int(sys.argv[1]) if len(sys.argv) > 1 else DIAS_ATRAS_DEFAULT
    max_articulos = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    print(f"Consultando Europe PMC: guias/consensos de mama desde hace {dias_atras} dia(s), max={max_articulos}...")
    articulos = buscar_actualizaciones(dias_atras, max_articulos)

    print(f"\nTotal de actualizaciones descargadas: {len(articulos)}")
    if articulos:
        primero = articulos[0]
        print(f"Ejemplo: PMID {primero.get('pmid')} - {primero.get('title')}")

    ruta = guardar_json(articulos)
    print(f"\nJSON crudo guardado en: {ruta}")


if __name__ == "__main__":
    main()
